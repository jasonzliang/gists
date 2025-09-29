#!/usr/bin/env python3
"""
Hades Save File Parser
Converts .sav files to JSON format and vice versa
Default format: Hades 2
"""

import struct
import json
import lz4.block
import argparse
import zlib
import tempfile
from pathlib import Path
from typing import Dict, Any, List, Union, Optional


class LuabinSerializer:
    """Handles serialization/deserialization of Luabin data"""

    # Lua type codes
    TYPE_NULL = 45
    TYPE_FALSE = 48
    TYPE_TRUE = 49
    TYPE_NUMBER = 78
    TYPE_STRING = 83
    TYPE_TABLE = 84

    @staticmethod
    def get_type_code(value: Any) -> int:
        """Get Lua type code for a Python value"""
        if value is None:
            return LuabinSerializer.TYPE_NULL
        elif value is True:
            return LuabinSerializer.TYPE_TRUE
        elif value is False:
            return LuabinSerializer.TYPE_FALSE
        elif isinstance(value, (int, float)):
            return LuabinSerializer.TYPE_NUMBER
        elif isinstance(value, str):
            return LuabinSerializer.TYPE_STRING
        elif isinstance(value, dict):
            return LuabinSerializer.TYPE_TABLE
        else:
            raise ValueError(f"Unsupported type for Lua serialization: {type(value)}")


class LuabinReader:
    """Reads Luabin binary data"""

    def __init__(self, buffer: bytes):
        self.buffer = buffer
        self.offset = 0

    def read_uint8(self) -> int:
        if self.offset >= len(self.buffer):
            raise ValueError("Buffer underrun: attempting to read beyond buffer")
        value = struct.unpack_from('<B', self.buffer, self.offset)[0]
        self.offset += 1
        return value

    def read_uint32(self) -> int:
        if self.offset + 4 > len(self.buffer):
            raise ValueError("Buffer underrun: attempting to read beyond buffer")
        value = struct.unpack_from('<I', self.buffer, self.offset)[0]
        self.offset += 4
        return value

    def read_double(self) -> float:
        if self.offset + 8 > len(self.buffer):
            raise ValueError("Buffer underrun: attempting to read beyond buffer")
        value = struct.unpack_from('<d', self.buffer, self.offset)[0]
        self.offset += 8
        return value

    def read_string(self) -> str:
        length = self.read_uint32()
        if length < 0 or self.offset + length > len(self.buffer):
            raise ValueError(f"Invalid string length: {length}")
        value = self.buffer[self.offset:self.offset + length].decode('utf-8')
        self.offset += length
        return value

    def read_value(self, type_code: int) -> Any:
        """Read a value based on its type code"""
        if type_code == LuabinSerializer.TYPE_NULL:
            return None
        elif type_code == LuabinSerializer.TYPE_FALSE:
            return False
        elif type_code == LuabinSerializer.TYPE_TRUE:
            return True
        elif type_code == LuabinSerializer.TYPE_NUMBER:
            return self.read_double()
        elif type_code == LuabinSerializer.TYPE_STRING:
            return self.read_string()
        elif type_code == LuabinSerializer.TYPE_TABLE:
            return self.read_table()
        else:
            raise ValueError(f"Unknown Luabin type code: {type_code}")

    def read_table(self) -> Dict[str, Any]:
        """Read a Lua table"""
        array_size = self.read_uint32()
        hash_size = self.read_uint32()
        total_size = array_size + hash_size

        if total_size > 10_000_000:  # Sanity check
            raise ValueError(f"Table size too large: {total_size}")

        table = {}
        for _ in range(total_size):
            key_type = self.read_uint8()
            key = self.read_value(key_type)
            value_type = self.read_uint8()
            value = self.read_value(value_type)
            table[str(key)] = value

        return table

    def parse(self) -> List[Any]:
        """Parse the entire Luabin data"""
        if len(self.buffer) == 0:
            return []

        length = self.read_uint8()
        data = []

        for _ in range(length):
            type_code = self.read_uint8()
            value = self.read_value(type_code)
            data.append(value)

        return data


class LuabinWriter:
    """Writes Luabin binary data"""

    def __init__(self):
        self.buffer = bytearray()

    def write_uint8(self, value: int):
        self.buffer.extend(struct.pack('<B', value))

    def write_uint32(self, value: int):
        self.buffer.extend(struct.pack('<I', value))

    def write_double(self, value: float):
        self.buffer.extend(struct.pack('<d', value))

    def write_string(self, value: str):
        encoded = value.encode('utf-8')
        self.write_uint32(len(encoded))
        self.buffer.extend(encoded)

    def write_value_data(self, type_code: int, value: Any):
        """Write value data without the type code prefix"""
        if type_code in [LuabinSerializer.TYPE_NULL, LuabinSerializer.TYPE_FALSE, LuabinSerializer.TYPE_TRUE]:
            pass  # No data for these types
        elif type_code == LuabinSerializer.TYPE_NUMBER:
            self.write_double(float(value))
        elif type_code == LuabinSerializer.TYPE_STRING:
            self.write_string(value)
        elif type_code == LuabinSerializer.TYPE_TABLE:
            self.write_table(value)

    def write_table(self, table: Dict[str, Any]):
        """Write a Lua table with proper array/hash separation"""
        # Separate numeric keys from string keys
        numeric_entries = []
        string_entries = []

        for key, value in table.items():
            try:
                num_key = float(key)
                if num_key.is_integer() and num_key > 0:
                    numeric_entries.append((int(num_key), key, value))
                else:
                    string_entries.append((key, value))
            except (ValueError, TypeError):
                string_entries.append((key, value))

        # Sort for consistent ordering
        numeric_entries.sort(key=lambda x: x[0])  # Sort by numeric value
        string_entries.sort(key=lambda x: x[0])   # Sort by string key

        # Write sizes
        self.write_uint32(len(numeric_entries))
        self.write_uint32(len(string_entries))

        # Write array part (numeric keys)
        for num_key, original_key, value in numeric_entries:
            key_type = LuabinSerializer.get_type_code(num_key)
            self.write_uint8(key_type)
            self.write_value_data(key_type, num_key)

            value_type = LuabinSerializer.get_type_code(value)
            self.write_uint8(value_type)
            self.write_value_data(value_type, value)

        # Write hash part (string keys)
        for str_key, value in string_entries:
            key_type = LuabinSerializer.get_type_code(str_key)
            self.write_uint8(key_type)
            self.write_value_data(key_type, str_key)

            value_type = LuabinSerializer.get_type_code(value)
            self.write_uint8(value_type)
            self.write_value_data(value_type, value)

    def serialize(self, data: List[Any]) -> bytes:
        """Serialize data to bytes"""
        self.buffer = bytearray()

        # Write number of entries
        self.write_uint8(len(data))

        # Write each entry
        for item in data:
            type_code = LuabinSerializer.get_type_code(item)
            self.write_uint8(type_code)
            self.write_value_data(type_code, item)

        return bytes(self.buffer)


class SaveFileStructure:
    """Defines save file structures for different Hades versions"""

    HADES_1_STRUCTURE = {
        "fields": [
            {"label": "signature", "type": "padding", "size": 4},
            {"label": "checksum", "type": "padding", "size": 4},
            {"label": "save_data", "type": "struct", "fields": [
                {"label": "version", "type": "int32"},
                {"label": "timestamp", "type": "int64"},
                {"label": "location", "type": "string"},
                {"label": "runs", "type": "int32"},
                {"label": "active_meta_points", "type": "int32"},
                {"label": "active_shrine_points", "type": "int32"},
                {"label": "god_mode_enabled", "type": "int8"},
                {"label": "hell_mode_enabled", "type": "int8"},
                {"label": "lua_keys", "type": "array", "content": {"type": "string"}},
                {"label": "current_map_name", "type": "string"},
                {"label": "start_next_map", "type": "string"},
                {"label": "luabin", "type": "array", "content": {"type": "int8"}}
            ]}
        ]
    }

    HADES_2_STRUCTURE = {
        "fields": [
            {"label": "signature", "type": "padding", "size": 4},
            {"label": "checksum", "type": "padding", "size": 4},
            {"label": "save_data", "type": "struct", "fields": [
                {"label": "version", "type": "int32"},
                {"label": "timestamp", "type": "int64"},
                {"label": "location", "type": "string"},
                {"label": "runs", "type": "int32"},
                {"label": "padding1", "type": "padding", "size": 8},
                {"label": "grasp", "type": "int32"},
                {"label": "prestige", "type": "int32"},
                {"label": "god_mode_enabled", "type": "int8"},
                {"label": "hell_mode_enabled", "type": "int8"},
                {"label": "lua_keys", "type": "array", "content": {"type": "string"}},
                {"label": "current_map_name", "type": "string"},
                {"label": "start_next_map", "type": "string"},
                {"label": "luabin", "type": "array", "content": {"type": "int8"}}
            ]}
        ]
    }

    @classmethod
    def get_structure(cls, is_hades_1: bool = False):
        return cls.HADES_1_STRUCTURE if is_hades_1 else cls.HADES_2_STRUCTURE


class SaveFileReader:
    """Reads Hades save files"""

    def __init__(self, buffer: bytes, is_hades_1: bool = False):
        self.buffer = buffer
        self.offset = 0
        self.structure = SaveFileStructure.get_structure(is_hades_1)
        self.data = {}

    def read_int8(self) -> int:
        if self.offset >= len(self.buffer):
            raise ValueError("Buffer underrun: attempting to read beyond buffer")
        value = struct.unpack_from('<B', self.buffer, self.offset)[0]
        self.offset += 1
        return value

    def read_int32(self) -> int:
        if self.offset + 4 > len(self.buffer):
            raise ValueError("Buffer underrun: attempting to read beyond buffer")
        value = struct.unpack_from('<i', self.buffer, self.offset)[0]
        self.offset += 4
        return value

    def read_int64(self) -> int:
        if self.offset + 8 > len(self.buffer):
            raise ValueError("Buffer underrun: attempting to read beyond buffer")
        value = struct.unpack_from('<Q', self.buffer, self.offset)[0]
        self.offset += 8
        return value

    def read_string(self) -> str:
        length = self.read_int32()
        if length < 0 or length > len(self.buffer) - self.offset:
            raise ValueError(f"Invalid string length: {length}")

        value = self.buffer[self.offset:self.offset + length].decode('utf-8')
        self.offset += length
        return value

    def read_array(self, content_type: str) -> List[Any]:
        length = self.read_int32()
        if length < 0 or length > 10_000_000:  # Sanity check
            raise ValueError(f"Invalid array length: {length}")

        array = []
        for _ in range(length):
            value = self.read_field(content_type)
            array.append(value)
        return array

    def read_padding(self, size: int) -> List[int]:
        if self.offset + size > len(self.buffer):
            raise ValueError("Buffer underrun: attempting to read beyond buffer")
        padding = []
        for _ in range(size):
            padding.append(self.read_int8())
        return padding

    def read_struct(self, fields: List[Dict]) -> Dict[str, Any]:
        struct_data = {}
        for field in fields:
            value = self.read_field(
                field["type"],
                field.get("content"),
                field.get("size", 0),
                field.get("fields", [])
            )
            struct_data[field["label"]] = value
        return struct_data

    def read_field(self, field_type: str, content: Optional[Dict] = None,
                  size: int = 0, fields: Optional[List] = None) -> Any:
        """Read a field based on its type"""
        if field_type == "int8":
            return self.read_int8()
        elif field_type == "int32":
            return self.read_int32()
        elif field_type == "int64":
            return self.read_int64()
        elif field_type == "string":
            return self.read_string()
        elif field_type == "array":
            return self.read_array(content["type"])
        elif field_type == "struct":
            return self.read_struct(fields)
        elif field_type == "padding":
            return self.read_padding(size)
        else:
            raise ValueError(f"Unknown field type: {field_type}")

    def decompress_luabin(self, luabin_data: List[int]) -> bytes:
        """Decompress LZ4 compressed luabin data"""
        if not luabin_data:
            return b""

        compressed = bytes(luabin_data)
        try:
            # Try to decompress with reasonable size limit
            max_size = len(compressed) * 50  # Allow up to 50x expansion
            decompressed = lz4.block.decompress(compressed, uncompressed_size=max_size)
            return decompressed
        except Exception as e:
            print(f"Warning: Failed to decompress luabin data: {e}")
            return b""

    def parse(self) -> Dict[str, Any]:
        """Parse the entire save file"""
        for field in self.structure["fields"]:
            value = self.read_field(
                field["type"],
                field.get("content"),
                field.get("size", 0),
                field.get("fields", [])
            )
            self.data[field["label"]] = value

        # Process luabin data if present
        if ("save_data" in self.data and
            "luabin" in self.data["save_data"] and
            self.data["save_data"]["luabin"]):

            luabin_raw = self.data["save_data"]["luabin"]
            try:
                decompressed = self.decompress_luabin(luabin_raw)
                if decompressed:
                    reader = LuabinReader(decompressed)
                    self.data["save_data"]["luabin"] = reader.parse()
            except Exception as e:
                print(f"Warning: Failed to parse luabin data: {e}")

        return self.data


class SaveFileWriter:
    """Writes Hades save files"""

    def __init__(self, is_hades_1: bool = False):
        self.structure = SaveFileStructure.get_structure(is_hades_1)
        self.buffer = bytearray()
        self.save_data_start = 0
        self.save_data_end = 0

    def write_int8(self, value: int):
        self.buffer.extend(struct.pack('<B', int(value)))

    def write_int32(self, value: int):
        self.buffer.extend(struct.pack('<i', int(value)))

    def write_int64(self, value: int):
        self.buffer.extend(struct.pack('<Q', int(value)))

    def write_string(self, value: str):
        encoded = str(value).encode('utf-8')
        self.write_int32(len(encoded))
        self.buffer.extend(encoded)

    def write_array(self, value: List[Any], content_type: str):
        self.write_int32(len(value))
        for item in value:
            self.write_field(content_type, item)

    def write_padding(self, value: List[int]):
        for byte_val in value:
            self.write_int8(byte_val)

    def write_struct(self, data: Dict[str, Any], fields: List[Dict]):
        # Track save_data boundaries for checksum calculation
        if any(field["label"] == "version" for field in fields):
            self.save_data_start = len(self.buffer)

        for field in fields:
            field_data = data.get(field["label"], self.get_default_value(field["type"]))
            self.write_field(
                field["type"],
                field_data,
                field.get("content"),
                field.get("size", 0),
                field.get("fields", [])
            )

        if any(field["label"] == "version" for field in fields):
            self.save_data_end = len(self.buffer)

    def get_default_value(self, field_type: str) -> Any:
        """Get default value for a field type"""
        defaults = {
            "int8": 0,
            "int32": 0,
            "int64": 0,
            "string": "",
            "array": [],
            "padding": []
        }
        return defaults.get(field_type, 0)

    def write_field(self, field_type: str, value: Any, content: Optional[Dict] = None,
                   size: int = 0, fields: Optional[List] = None):
        """Write a field based on its type"""
        if field_type == "int8":
            self.write_int8(value)
        elif field_type == "int32":
            self.write_int32(value)
        elif field_type == "int64":
            self.write_int64(value)
        elif field_type == "string":
            self.write_string(value)
        elif field_type == "array":
            self.write_array(value, content["type"])
        elif field_type == "struct":
            self.write_struct(value, fields)
        elif field_type == "padding":
            if isinstance(value, list):
                self.write_padding(value)
            else:
                self.write_padding([0] * size)
        else:
            raise ValueError(f"Unknown field type: {field_type}")

    def compress_luabin(self, luabin_data: List[Any]) -> List[int]:
        """Compress luabin data using LZ4"""
        writer = LuabinWriter()
        uncompressed = writer.serialize(luabin_data)

        try:
            # Use fastest compression for consistency
            compressed = lz4.block.compress(uncompressed, store_size=False, compression=0)
            return list(compressed)
        except Exception as e:
            print(f"Warning: Failed to compress luabin data: {e}")
            return list(uncompressed)

    def calculate_checksum(self, data: bytes) -> int:
        """Calculate Adler-32 checksum"""
        return zlib.adler32(data) & 0xffffffff

    def serialize(self, data: Dict[str, Any]) -> bytes:
        """Serialize data to bytes"""
        self.buffer = bytearray()

        # Process luabin data if it needs compression
        processed_data = data.copy()
        if self.needs_luabin_compression(data):
            luabin_compressed = self.compress_luabin(data["save_data"]["luabin"])
            processed_data = self.deep_copy_with_luabin(data, luabin_compressed)

        # Ensure required fields exist
        self.ensure_required_fields(processed_data)

        # Write all fields
        for field in self.structure["fields"]:
            field_data = processed_data.get(field["label"], self.get_default_value(field["type"]))
            self.write_field(
                field["type"],
                field_data,
                field.get("content"),
                field.get("size", 0),
                field.get("fields", [])
            )

        # Calculate and write checksum
        if self.save_data_start < self.save_data_end:
            save_data_bytes = self.buffer[self.save_data_start:self.save_data_end]
            checksum = self.calculate_checksum(save_data_bytes)
            struct.pack_into('<I', self.buffer, 4, checksum)

        return bytes(self.buffer)

    def needs_luabin_compression(self, data: Dict[str, Any]) -> bool:
        """Check if luabin data needs compression"""
        return (
            "save_data" in data and
            "luabin" in data["save_data"] and
            isinstance(data["save_data"]["luabin"], list) and
            data["save_data"]["luabin"] and
            isinstance(data["save_data"]["luabin"][0], dict)
        )

    def deep_copy_with_luabin(self, data: Dict[str, Any], luabin_compressed: List[int]) -> Dict[str, Any]:
        """Create a deep copy with compressed luabin"""
        import copy
        processed_data = copy.deepcopy(data)
        processed_data["save_data"]["luabin"] = luabin_compressed
        return processed_data

    def ensure_required_fields(self, data: Dict[str, Any]):
        """Ensure required fields exist with defaults"""
        if "signature" not in data:
            data["signature"] = [83, 65, 86, 69]  # "SAVE" in ASCII
        if "checksum" not in data:
            data["checksum"] = [0, 0, 0, 0]  # Will be calculated


class HadesSaveConverter:
    """Main class for save file conversion operations"""

    @staticmethod
    def parse_save_file(file_path: Union[str, Path], is_hades_1: bool = False) -> Dict[str, Any]:
        """Parse a save file and return data as dictionary"""
        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"Save file not found: {file_path}")

        with open(file_path, 'rb') as f:
            buffer = f.read()

        reader = SaveFileReader(buffer, is_hades_1)
        return reader.parse()

    @staticmethod
    def write_save_file(data: Dict[str, Any], file_path: Union[str, Path], is_hades_1: bool = False):
        """Write data to a save file"""
        file_path = Path(file_path)

        writer = SaveFileWriter(is_hades_1)
        binary_data = writer.serialize(data)

        with open(file_path, 'wb') as f:
            f.write(binary_data)

        print(f"Written {len(binary_data)} bytes to {file_path}")

    @staticmethod
    def validate_save_file(file_path: Union[str, Path], is_hades_1: bool = False) -> bool:
        """Validate a save file by attempting to parse it"""
        try:
            data = HadesSaveConverter.parse_save_file(file_path, is_hades_1)

            if "save_data" not in data:
                print("ERROR: Missing save_data section")
                return False

            save_data = data["save_data"]

            # Check required fields
            required_fields = [
                "version", "timestamp", "location", "runs",
                "god_mode_enabled", "hell_mode_enabled",
                "lua_keys", "current_map_name", "start_next_map", "luabin"
            ]

            if is_hades_1:
                required_fields.extend(["active_meta_points", "active_shrine_points"])
            else:
                required_fields.extend(["padding1", "grasp", "prestige"])

            missing = [f for f in required_fields if f not in save_data]
            if missing:
                print(f"ERROR: Missing fields: {', '.join(missing)}")
                return False

            # Type validation (critical fields only)
            type_checks = [
                ("version", int), ("timestamp", int), ("runs", int),
                ("location", str), ("lua_keys", list), ("luabin", list),
                ("current_map_name", str), ("start_next_map", str),
                ("god_mode_enabled", int), ("hell_mode_enabled", int)
            ]

            if is_hades_1:
                type_checks.extend([("active_meta_points", int), ("active_shrine_points", int)])
            else:
                type_checks.extend([("grasp", int), ("prestige", int), ("padding1", list)])

            for field, expected_type in type_checks:
                if field in save_data and not isinstance(save_data[field], expected_type):
                    print(f"ERROR: {field} must be {expected_type.__name__}")
                    return False

            # Validate luabin structure
            luabin = save_data["luabin"]
            if luabin and isinstance(luabin[0], dict):
                first_entry = luabin[0]
                if "CurrentRun" not in first_entry or not isinstance(first_entry["CurrentRun"], dict):
                    print("WARNING: Invalid luabin structure")

            print("✅ Save file validation passed")

            # Compact summary
            sd = save_data
            print(f"Format: {'Hades 1' if is_hades_1 else 'Hades 2'}, "
                  f"Version: {sd.get('version')}, Runs: {sd.get('runs')}, "
                  f"Location: {sd.get('location')}")

            return True

        except Exception as e:
            print(f"ERROR: Validation failed: {e}")
            return False

    @staticmethod
    def round_trip_test(file_path: Union[str, Path], is_hades_1: bool = False, keep_temp: bool = False):
        """Test round-trip conversion (sav → json → sav)"""
        file_path = Path(file_path)

        print(f"Testing round-trip conversion: {file_path}")

        if keep_temp:
            temp_json = file_path.with_suffix('.temp.json')
            temp_sav = file_path.with_suffix('.temp.sav')
            temp_dir = None
        else:
            temp_dir = tempfile.TemporaryDirectory()
            temp_json = Path(temp_dir.name) / "temp.json"
            temp_sav = Path(temp_dir.name) / "temp.sav"

        try:
            # Step 1: Parse original
            print("Step 1: Parsing original save file...")
            original_data = HadesSaveConverter.parse_save_file(file_path, is_hades_1)

            # Step 2: Save to JSON
            print("Step 2: Converting to JSON...")
            with open(temp_json, 'w', encoding='utf-8') as f:
                json.dump(original_data, f, indent=2)

            # Step 3: Load from JSON and save as .sav
            print("Step 3: Converting back to .sav...")
            with open(temp_json, 'r', encoding='utf-8') as f:
                loaded_data = json.load(f)

            HadesSaveConverter.write_save_file(loaded_data, temp_sav, is_hades_1)

            # Step 4: Validate
            print("Step 4: Validating result...")
            is_valid = HadesSaveConverter.validate_save_file(temp_sav, is_hades_1)

            if is_valid:
                print("✅ Round-trip test PASSED!")

                # Compare sizes
                original_size = file_path.stat().st_size
                new_size = temp_sav.stat().st_size
                print(f"Original size: {original_size} bytes")
                print(f"New size: {new_size} bytes")
                print(f"Size difference: {new_size - original_size:+d} bytes")

                if keep_temp:
                    print(f"Temporary files: {temp_json}, {temp_sav}")

                return True
            else:
                print("❌ Round-trip test FAILED!")
                return False

        finally:
            if temp_dir:
                temp_dir.cleanup()


def main():
    parser = argparse.ArgumentParser(
        description='Hades Save File Converter - Convert between .sav and JSON formats (Hades 2 by default)'
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Parse command (sav to json)
    parse_parser = subparsers.add_parser('parse', help='Convert .sav file to JSON')
    parse_parser.add_argument('input_file', help='Path to the .sav file')
    parse_parser.add_argument('-o', '--output', help='Output JSON file path (default: input_file.json)')
    parse_parser.add_argument('--hades1', action='store_true', help='Use Hades 1 save format (default: Hades 2)')
    parse_parser.add_argument('--indent', type=int, default=2, help='JSON indentation (default: 2)')

    # Build command (json to sav)
    build_parser = subparsers.add_parser('build', help='Convert JSON file to .sav')
    build_parser.add_argument('input_file', help='Path to the JSON file')
    build_parser.add_argument('-o', '--output', help='Output .sav file path (default: input_file.sav)')
    build_parser.add_argument('--hades1', action='store_true', help='Use Hades 1 save format (default: Hades 2)')

    # Validate command
    validate_parser = subparsers.add_parser('validate', help='Validate a .sav file')
    validate_parser.add_argument('input_file', help='Path to the .sav file')
    validate_parser.add_argument('--hades1', action='store_true', help='Use Hades 1 save format (default: Hades 2)')

    # Test command (round-trip)
    test_parser = subparsers.add_parser('test', help='Test round-trip conversion (sav→json→sav)')
    test_parser.add_argument('input_file', help='Path to the .sav file')
    test_parser.add_argument('--hades1', action='store_true', help='Use Hades 1 save format (default: Hades 2)')
    test_parser.add_argument('--keep-temp', action='store_true', help='Keep temporary files')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    input_path = Path(args.input_file)
    is_hades_1 = args.hades1

    try:
        if args.command == 'parse':
            # Convert .sav to JSON
            output_path = Path(args.output) if args.output else input_path.with_suffix('.json')

            print(f"Parsing save file: {input_path}")
            save_data = HadesSaveConverter.parse_save_file(input_path, is_hades_1)

            print(f"Writing JSON to: {output_path}")
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, indent=args.indent, ensure_ascii=False)

            print("Conversion completed successfully!")

            # Print basic info
            if 'save_data' in save_data:
                sd = save_data['save_data']
                print(f"\nSave Info:")
                print(f"  Format: {'Hades 1' if is_hades_1 else 'Hades 2'}")
                print(f"  Version: {sd.get('version', 'Unknown')}")
                print(f"  Runs: {sd.get('runs', 'Unknown')}")
                print(f"  Location: {sd.get('location', 'Unknown')}")
                if 'luabin' in sd and sd['luabin']:
                    print(f"  Luabin entries: {len(sd['luabin'])}")

        elif args.command == 'build':
            # Convert JSON to .sav
            output_path = Path(args.output) if args.output else input_path.with_suffix('.sav')

            print(f"Loading JSON file: {input_path}")
            with open(input_path, 'r', encoding='utf-8') as f:
                save_data = json.load(f)

            print(f"Building save file: {output_path}")
            HadesSaveConverter.write_save_file(save_data, output_path, is_hades_1)

            print("Conversion completed successfully!")

            # Validate the created file
            print("Validating created save file...")
            HadesSaveConverter.validate_save_file(output_path, is_hades_1)

            # Print basic info
            if 'save_data' in save_data:
                sd = save_data['save_data']
                print(f"\nSave Info:")
                print(f"  Format: {'Hades 1' if is_hades_1 else 'Hades 2'}")
                print(f"  Version: {sd.get('version', 'Unknown')}")
                print(f"  Runs: {sd.get('runs', 'Unknown')}")
                print(f"  Location: {sd.get('location', 'Unknown')}")

        elif args.command == 'validate':
            # Validate a save file
            print(f"Validating save file: {input_path}")
            is_valid = HadesSaveConverter.validate_save_file(input_path, is_hades_1)
            if not is_valid:
                return 1

        elif args.command == 'test':
            # Round-trip test
            success = HadesSaveConverter.round_trip_test(input_path, is_hades_1, args.keep_temp)
            if not success:
                return 1

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit(main())