import torch
import torch.nn as nn
import torch.nn.functional as F
import math
import time
import os
import requests
from torch.utils.data import Dataset, DataLoader

# ==========================================
# 1. Configuration & Fairness
# ==========================================
CONFIG = {
    'seed': 42,
    'seq_len': 128,
    'batch_size': 32,      # Slightly higher batch size for stability
    'vocab_size': 10000,
    'epochs': 2,

    # Auto-detect Device: CUDA (Linux/Win) or MPS (Mac) or CPU
    'device': torch.device(
        "cuda" if torch.cuda.is_available() else
        "mps" if torch.backends.mps.is_available() else "cpu"
    ),

    # --- TARGET: ~3.5M Parameters for both ---

    # Baseline
    'transformer': {
        'd_model': 256,
        'n_layers': 6,
        'n_heads': 4,
        'dropout': 0.1
    },

    # TPUM (d_model reduced to 224 to account for extra gating params)
    'tpum': {
        'd_model': 224,
        'n_layers': 6,
        'n_heads': 4,
        'window_size': 32, # Local Path
        'd_state': 64,     # State Path
        'memory_slots': 16,# Global Path
        'dropout': 0.1
    }
}

print(f"Running on device: {CONFIG['device']}")
torch.manual_seed(CONFIG['seed'])

# ==========================================
# 2. Architecture: TPUM (Bug-Checked)
# ==========================================

class TPUMRouter(nn.Module):
    def __init__(self, d_model):
        super().__init__()
        self.router_head = nn.Linear(d_model, 3)

    def forward(self, x):
        # x: [B, T, D] -> [B, T, 3]
        logits = self.router_head(x)
        return F.softmax(logits, dim=-1)

class LocalWindowAttention(nn.Module):
    """Path 1: Local Windowed Attention"""
    def __init__(self, d_model, num_heads, window_size, dropout=0.1):
        super().__init__()
        self.num_heads = num_heads
        self.d_head = d_model // num_heads
        self.window_size = window_size
        self.scale = self.d_head ** -0.5

        self.qkv = nn.Linear(d_model, 3 * d_model)
        self.out_proj = nn.Linear(d_model, d_model)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        B, T, D = x.size()
        qkv = self.qkv(x).chunk(3, dim=-1)
        q, k, v = map(lambda t: t.view(B, T, self.num_heads, self.d_head).transpose(1, 2), qkv)

        # --- Robust Window Masking ---
        # 1. Causal Mask (Upper triangle is blocked)
        causal_mask = torch.triu(torch.ones(T, T, device=x.device), diagonal=1).bool()

        # 2. Local Window Mask (Lower triangle beyond window is blocked)
        # We want to block where (row_idx - col_idx) > window_size
        # Equivalent to: col_idx < row_idx - window_size
        # This corresponds to tril(ones, diagonal = -window_size - 1)
        too_old_mask = torch.tril(torch.ones(T, T, device=x.device), diagonal=-(self.window_size + 1)).bool()

        combined_mask = causal_mask | too_old_mask

        # Apply mask
        attn = (q @ k.transpose(-2, -1)) * self.scale
        attn = attn.masked_fill(combined_mask, float('-inf'))

        attn = F.softmax(attn, dim=-1)
        attn = self.dropout(attn)

        out = (attn @ v).transpose(1, 2).contiguous().view(B, T, D)
        return self.out_proj(out)

class SimplifiedStatePath(nn.Module):
    """Path 2: SSM Proxy (Linear Recurrent Unit)"""
    def __init__(self, d_model, d_state):
        super().__init__()
        self.d_state = d_state
        self.in_proj = nn.Linear(d_model, d_state)
        # Learnable decay (sigmoid constrained) and gain
        self.lambda_param = nn.Parameter(torch.randn(d_state))
        self.gamma_param = nn.Parameter(torch.randn(d_state))
        self.out_proj = nn.Linear(d_state, d_model)

    def forward(self, x, state=None):
        B, T, D = x.size()
        u = self.in_proj(x) # [B, T, D_State]

        if state is None:
            state = torch.zeros(B, self.d_state, device=x.device)

        decay = torch.sigmoid(self.lambda_param)
        gain = self.gamma_param

        h_vals = []
        curr_state = state

        # Sequential scan (Slow in PyTorch, but accurate for logic verification)
        # In production, use Mamba's selective_scan_cuda
        for t in range(T):
            curr_state = decay * curr_state + gain * u[:, t, :]
            h_vals.append(curr_state)

        h_stack = torch.stack(h_vals, dim=1)
        return self.out_proj(h_stack), curr_state

class GlobalMemoryPath(nn.Module):
    """Path 3: Global Memory with Gated R/W"""
    def __init__(self, d_model, num_heads, memory_slots=16, dropout=0.1):
        super().__init__()
        self.memory_slots = memory_slots
        self.num_heads = num_heads

        # READ: Cross Attention
        self.read_attn = nn.MultiheadAttention(d_model, num_heads, dropout=dropout, batch_first=True)

        # WRITE: Gated Update
        self.write_gate = nn.Linear(d_model, 1)
        self.mem_query = nn.Linear(d_model, memory_slots)

    def forward(self, x, memory_bank=None):
        B, T, D = x.size()
        if memory_bank is None:
            memory_bank = torch.zeros(B, self.memory_slots, D, device=x.device)

        # 1. READ
        # Query=x, Key/Val=memory_bank
        # Attn mask? No, we attend to all memory slots.
        read_out, _ = self.read_attn(x, memory_bank, memory_bank)

        # 2. WRITE
        # Simple attention pooling to find what to write into K slots
        # Weights: [B, K, T]
        scores = torch.matmul(memory_bank, x.transpose(1, 2)) / math.sqrt(D)
        pooling_weights = F.softmax(scores, dim=-1)
        candidates = torch.matmul(pooling_weights, x) # [B, K, D]

        # Gated Update
        update_gate = torch.sigmoid(self.write_gate(candidates)) # [B, K, 1]
        new_memory_bank = (1 - update_gate) * memory_bank + update_gate * candidates

        return read_out, new_memory_bank

class TPUMBlock(nn.Module):
    def __init__(self, cfg):
        super().__init__()
        d_model = cfg['d_model']
        self.pre_norm = nn.LayerNorm(d_model)
        self.router = TPUMRouter(d_model)

        self.local = LocalWindowAttention(d_model, cfg['n_heads'], cfg['window_size'], cfg['dropout'])
        self.state = SimplifiedStatePath(d_model, cfg['d_state'])
        self.global_path = GlobalMemoryPath(d_model, cfg['n_heads'], cfg['memory_slots'], cfg['dropout'])

        self.dropout = nn.Dropout(cfg['dropout'])
        self.ffn_norm = nn.LayerNorm(d_model)
        self.ffn = nn.Sequential(
            nn.Linear(d_model, 4 * d_model),
            nn.GELU(),
            nn.Linear(4 * d_model, d_model),
            nn.Dropout(cfg['dropout'])
        )

    def forward(self, x, s_prev, m_prev):
        residual = x
        x_norm = self.pre_norm(x)

        # Route
        probs = self.router(x_norm) # [B, T, 3]

        # Execute Paths
        y_l = self.local(x_norm)
        y_s, s_next = self.state(x_norm, s_prev)
        y_g, m_next = self.global_path(x_norm, m_prev)

        # Mix
        g_l, g_s, g_g = probs[:,:,0:1], probs[:,:,1:2], probs[:,:,2:3]
        mixed = (g_l * y_l) + (g_s * y_s) + (g_g * y_g)

        x = residual + self.dropout(mixed)
        x = x + self.ffn(self.ffn_norm(x))

        return x, s_next, m_next, probs

class TPUMModel(nn.Module):
    def __init__(self, vocab_size, cfg):
        super().__init__()
        self.cfg = cfg
        self.emb = nn.Embedding(vocab_size, cfg['d_model'])
        self.layers = nn.ModuleList([TPUMBlock(cfg) for _ in range(cfg['n_layers'])])
        self.final_norm = nn.LayerNorm(cfg['d_model'])
        self.head = nn.Linear(cfg['d_model'], vocab_size)

    def forward(self, x, states=None, memories=None):
        B, T = x.shape
        x = self.emb(x)

        new_states, new_memories, all_probs = [], [], []

        if states is None:
            states = [None] * len(self.layers)
            memories = [None] * len(self.layers)

        for i, layer in enumerate(self.layers):
            x, s, m, p = layer(x, states[i], memories[i])
            new_states.append(s)
            new_memories.append(m)
            all_probs.append(p)

        return self.head(self.final_norm(x)), new_states, new_memories, all_probs

# ==========================================
# 3. Baseline: Standard Transformer
# ==========================================
class StandardTransformer(nn.Module):
    def __init__(self, vocab_size, d_model, n_layers, n_heads, dropout):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, d_model)
        self.pos_encoder = nn.Parameter(torch.zeros(1, 1024, d_model))
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=n_heads, dim_feedforward=4*d_model,
            dropout=dropout, batch_first=True, norm_first=True
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)
        self.norm = nn.LayerNorm(d_model)
        self.head = nn.Linear(d_model, vocab_size)

    def forward(self, x):
        B, T = x.shape
        # Add Pos Enc
        x = self.embedding(x) + self.pos_encoder[:, :T, :]

        # Causal Mask
        mask = nn.Transformer.generate_square_subsequent_mask(T).to(x.device)

        x = self.transformer(x, mask=mask, is_causal=True)
        return self.head(self.norm(x))

# ==========================================
# 4. Data Loading (Robust Raw Download)
# ==========================================
class WikiText2Dataset(Dataset):
    def __init__(self, split='train', seq_len=128, vocab_size=10000):
        self.seq_len = seq_len
        self.data = self._download_and_process(split, vocab_size)

    def _download_and_process(self, split, vocab_size):
        # Stable Raw URL from PyTorch Examples
        base_url = "https://raw.githubusercontent.com/pytorch/examples/master/word_language_model/data/wikitext-2/"
        file_map = {
            'train': 'train.txt',
            'valid': 'valid.txt',
            'test': 'test.txt'
        }
        filename = file_map.get(split, 'train.txt')

        print(f"Loading/Downloading {filename}...")

        if not os.path.exists(filename):
            r = requests.get(base_url + filename)
            if r.status_code != 200:
                # Fallback for filenames sometimes differing in mirrors
                if split == 'train': filename = 'wiki.train.tokens'
                elif split == 'valid': filename = 'wiki.valid.tokens'
                r = requests.get(base_url + filename)

            with open(filename, 'wb') as f:
                f.write(r.content)
            print("Download complete.")

        with open(filename, 'r', encoding='utf-8') as f:
            text = f.read()

        # Simple Tokenization
        tokens = text.split()
        from collections import Counter
        common = Counter(tokens).most_common(vocab_size - 1)
        self.vocab = {word: i for i, (word, _) in enumerate(common)}
        self.vocab['<unk>'] = vocab_size - 1

        return torch.tensor([self.vocab.get(t, self.vocab['<unk>']) for t in tokens], dtype=torch.long)

    def __len__(self):
        return (len(self.data) - 1) // self.seq_len

    def __getitem__(self, idx):
        start = idx * self.seq_len
        chunk = self.data[start : start + self.seq_len + 1]
        return chunk[:-1], chunk[1:]

# ==========================================
# 5. Training Engine
# ==========================================
def count_params(model):
    return sum(p.numel() for p in model.parameters() if p.requires_grad)

def train_eval(model_name, model, train_dl, valid_dl):
    print(f"\n--- {model_name} [Params: {count_params(model):,}] ---")
    opt = torch.optim.AdamW(model.parameters(), lr=3e-4)
    crit = nn.CrossEntropyLoss()

    model.train()
    start = time.time()

    # Track Memory
    peak_mem = 0

    for epoch in range(CONFIG['epochs']):
        for i, (x, y) in enumerate(train_dl):
            x, y = x.to(CONFIG['device']), y.to(CONFIG['device'])
            opt.zero_grad()

            if CONFIG['device'].type == 'cuda':
                torch.cuda.reset_peak_memory_stats()

            if model_name == "Transformer":
                logits = model(x)
                loss = crit(logits.reshape(-1, CONFIG['vocab_size']), y.reshape(-1))
            else:
                # TPUM (reset state per batch for fairness/simplicity in this demo)
                logits, _, _, probs = model(x, None, None)
                loss_lm = crit(logits.reshape(-1, CONFIG['vocab_size']), y.reshape(-1))

                # Lagrangian Cost: Penalize Global Usage
                cost = 0.05 * torch.stack([p[:,:,2].mean() for p in probs]).mean()
                loss = loss_lm + cost

            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()

            # Memory Check
            if CONFIG['device'].type == 'cuda':
                current_mem = torch.cuda.max_memory_allocated() / 1024**2
                peak_mem = max(peak_mem, current_mem)
            # Note: PyTorch doesn't expose peak_memory_allocated for MPS yet reliably

            if i % 50 == 0:
                print(f"Ep {epoch+1} | Step {i} | Loss: {loss.item():.4f}")

    print(f"Training Time: {time.time() - start:.2f}s")

    # Eval
    model.eval()
    total_nll, tokens = 0, 0
    with torch.no_grad():
        for x, y in valid_dl:
            x, y = x.to(CONFIG['device']), y.to(CONFIG['device'])
            if model_name == "Transformer":
                logits = model(x)
            else:
                logits, _, _, _ = model(x, None, None)

            nll = F.cross_entropy(logits.reshape(-1, CONFIG['vocab_size']), y.reshape(-1), reduction='sum')
            total_nll += nll.item()
            tokens += y.numel()

    ppl = math.exp(total_nll / tokens)
    return ppl, peak_mem, count_params(model)

# ==========================================
# 6. Main
# ==========================================
if __name__ == "__main__":
    train_ds = WikiText2Dataset('train', CONFIG['seq_len'], CONFIG['vocab_size'])
    valid_ds = WikiText2Dataset('valid', CONFIG['seq_len'], CONFIG['vocab_size'])

    train_dl = DataLoader(train_ds, batch_size=CONFIG['batch_size'], shuffle=True)
    valid_dl = DataLoader(valid_ds, batch_size=CONFIG['batch_size'])

    # 1. Transformer
    tf_model = StandardTransformer(CONFIG['vocab_size'], **CONFIG['transformer']).to(CONFIG['device'])
    tf_ppl, tf_mem, tf_p = train_eval("Transformer", tf_model, train_dl, valid_dl)

    # 2. TPUM
    tpum_model = TPUMModel(CONFIG['vocab_size'], CONFIG['tpum']).to(CONFIG['device'])
    tpum_ppl, tpum_mem, tpum_p = train_eval("TPUM", tpum_model, train_dl, valid_dl)

    print("\n" + "="*45)
    print(f"{'METRIC':<15} | {'TRANSFORMER':<12} | {'TPUM (Ours)':<12}")
    print("-" * 45)
    print(f"{'Parameters':<15} | {tf_p/1e6:.2f}M        | {tpum_p/1e6:.2f}M")
    print(f"{'Perplexity':<15} | {tf_ppl:.2f}        | {tpum_ppl:.2f}")
    if CONFIG['device'].type == 'cuda':
        print(f"{'Peak Memory':<15} | {tf_mem:.1f} MB       | {tpum_mem:.1f} MB")
    print("="*45)