// Main module for manga speech bubble detection
// Requires: tesseract.js and opencv.js

// Main function to process manga page
async function processMangaPage(imageSrc) {
  // Safely create UI elements for visualization
  const resultContainer = document.getElementById('result-container');
  if (!resultContainer) {
    console.error('Result container element not found');
    return [];
  }

  // Clear previous results
  resultContainer.innerHTML = '';

  const statusDiv = document.createElement('div');
  statusDiv.id = 'status';
  resultContainer.appendChild(statusDiv);

  updateStatus('Loading image...');

  try {
    // Ensure OpenCV is ready before proceeding
    await ensureOpenCVReady();

    // Load image using improved approach
    const imgCanvas = await loadImageToCanvas(imageSrc);

    // Display the image
    const displayImg = document.createElement('img');
    displayImg.src = imageSrc;
    displayImg.style.maxWidth = '100%';
    resultContainer.appendChild(displayImg);

    // Read image into OpenCV format with error handling
    let src;
    try {
      src = cv.imread(imgCanvas);
    } catch (error) {
      console.error('Error with cv.imread:', error);
      // Try alternative approach with ImageData
      const ctx = imgCanvas.getContext('2d');
      const imgData = ctx.getImageData(0, 0, imgCanvas.width, imgCanvas.height);
      src = cv.matFromImageData(imgData);
    }

    // Detect speech bubbles
    updateStatus('Detecting speech bubbles...');
    const bubbles = await detectSpeechBubbles(src);

    // Process each bubble
    updateStatus(`Found ${bubbles.length} potential speech bubbles`);
    const results = [];

    for (let i = 0; i < bubbles.length; i++) {
      updateStatus(`Processing bubble ${i+1}/${bubbles.length}`);
      const bubble = bubbles[i];

      // Extract bubble region
      const { x, y, width, height } = bubble.box;
      const roi = new cv.Mat();
      const rect = new cv.Rect(x, y, width, height);

      // Safely handle region extraction
      try {
        const roiSrc = src.roi(rect);
        roiSrc.copyTo(roi);
      } catch (error) {
        console.warn(`Failed to extract region for bubble ${i+1}:`, error);
        continue; // Skip this bubble and move to next
      }

      // Convert ROI to image data for Tesseract
      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      cv.imshow(canvas, roi);

      // Determine text orientation
      const orientation = await determineTextOrientation(canvas);

      // Perform OCR on the bubble
      const ocrResult = await performOCR(canvas, orientation);

      results.push({
        box: bubble.box,
        orientation: orientation,
        text: ocrResult.text,
        confidence: ocrResult.confidence
      });

      // Clean up
      roi.delete();
    }

    // Visualize results
    updateStatus('Visualizing results...');
    visualizeResults(src, results);

    // Display OCR results
    const textResultsDiv = document.createElement('div');
    textResultsDiv.className = 'text-results';
    const textResultsHeading = document.createElement('h3');
    textResultsHeading.textContent = 'Extracted Text';
    textResultsDiv.appendChild(textResultsHeading);

    results.forEach((result, i) => {
      const bubbleText = document.createElement('div');
      bubbleText.className = 'bubble-text';
      bubbleText.innerHTML = `<strong>Bubble #${i+1} (${result.orientation}°)</strong><br>${result.text || '[No text detected]'}<br><small>Confidence: ${result.confidence?.toFixed(2) || 'N/A'}%</small>`;
      textResultsDiv.appendChild(bubbleText);
    });

    resultContainer.appendChild(textResultsDiv);

    // Clean up OpenCV memory
    src.delete();

    updateStatus('Processing complete!');
    return results;

  } catch (error) {
    updateStatus(`Error: ${error.message}`);
    console.error('Processing error:', error);
    return [];
  }
}

// Ensure OpenCV is loaded and ready
function ensureOpenCVReady() {
  return new Promise((resolve, reject) => {
    if (window.cv && typeof cv.getBuildInformation === 'function') {
      // OpenCV is already loaded and ready
      resolve();
      return;
    }

    // Check if script is already being loaded
    const existingScript = document.querySelector('script[src*="opencv.js"]');
    if (existingScript) {
      // Script is loading, wait for it
      const checkInterval = setInterval(() => {
        if (window.cv && typeof cv.getBuildInformation === 'function') {
          clearInterval(checkInterval);
          resolve();
        }
      }, 100);

      // Add a timeout to prevent indefinite waiting
      setTimeout(() => {
        clearInterval(checkInterval);
        if (window.cv && typeof cv.getBuildInformation === 'function') {
          resolve();
        } else {
          console.warn("OpenCV initialization timed out, attempting to continue anyway");
          resolve(); // Try to continue anyway
        }
      }, 5000);
      return;
    }

    // Since the script is included in your HTML, we shouldn't need to add it again
    // Just wait for it to be initialized

    // Set up a timeout for OpenCV initialization
    const maxWaitTime = 10000; // 10 seconds
    const startTime = Date.now();

    const checkOpenCV = () => {
      if (window.cv && typeof cv.getBuildInformation === 'function') {
        console.log("OpenCV is ready");
        resolve();
        return true;
      }

      const elapsedTime = Date.now() - startTime;
      if (elapsedTime > maxWaitTime) {
        console.warn("OpenCV initialization timed out, attempting to continue anyway");
        resolve(); // Try to continue anyway
        return true;
      }

      return false;
    };

    // Check immediately
    if (!checkOpenCV()) {
      // If not ready, set up an interval to check periodically
      const checkInterval = setInterval(() => {
        if (checkOpenCV()) {
          clearInterval(checkInterval);
        }
      }, 100);
    }
  });
}

// Load image to canvas for better compatibility with OpenCV
function loadImageToCanvas(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = 'anonymous'; // Handle CORS issues

    img.onload = () => {
      // Create a canvas with image dimensions
      const canvas = document.createElement('canvas');
      canvas.width = img.width;
      canvas.height = img.height;

      // Draw the image on the canvas
      const ctx = canvas.getContext('2d');
      ctx.drawImage(img, 0, 0);

      resolve(canvas);
    };

    img.onerror = () => reject(new Error('Failed to load image'));
    img.src = src;
  });
}

// Detect speech bubbles using OpenCV.js - with debugging and adjusted parameters
async function detectSpeechBubbles(src) {
  // Create working matrices
  const gray = new cv.Mat();
  const binary = new cv.Mat();
  const dilated = new cv.Mat();
  const eroded = new cv.Mat();
  let contours = new cv.MatVector();
  let hierarchy = new cv.Mat();
  let kernel = null;

  // Parameters that can be adjusted - make these accessible to visualizeResults
  // by making them properties of the window object
  window.BUBBLE_PARAMS = {
    // Preprocessing parameters
    ADAPTIVE_BLOCK_SIZE: 9,    // Block size for adaptive threshold (must be odd)
    ADAPTIVE_C: 2,             // Constant subtracted from mean
    MORPH_KERNEL_SIZE: 3,      // Size of kernel for morphological operations
    DILATE_ITERATIONS: 1,      // Number of dilation iterations
    ERODE_ITERATIONS: 1,       // Number of erosion iterations

    // Filtering parameters
    MIN_AREA_RATIO: 0.0005,    // Minimum bubble area as % of image (0.05%)
    MAX_AREA_RATIO: 0.95,      // Maximum bubble area as % of image (95%)
    MIN_CIRCULARITY: 0.02,     // Minimum circularity (0 = line, 1 = perfect circle)
    MIN_ASPECT_RATIO: 0.15,    // Minimum aspect ratio (width/height)
    MAX_ASPECT_RATIO: 6.0,     // Maximum aspect ratio
    MIN_TEXT_DENSITY: 0.001,   // Minimum text pixel density in bubble
    MAX_TEXT_DENSITY: 0.9,     // Maximum text pixel density in bubble
  };

  // Calculate actual thresholds
  const imgArea = src.rows * src.cols;
  const minArea = imgArea * window.BUBBLE_PARAMS.MIN_AREA_RATIO;
  const maxArea = imgArea * window.BUBBLE_PARAMS.MAX_AREA_RATIO;

  console.log("=== BUBBLE DETECTION PARAMETERS ===");
  console.log(`Image dimensions: ${src.cols}x${src.rows}, Area: ${imgArea}px²`);
  console.log(`Area thresholds: Min=${minArea}px² (${window.BUBBLE_PARAMS.MIN_AREA_RATIO*100}%), Max=${maxArea}px² (${window.BUBBLE_PARAMS.MAX_AREA_RATIO*100}%)`);
  console.log(`Adaptive threshold: Block size=${window.BUBBLE_PARAMS.ADAPTIVE_BLOCK_SIZE}, C=${window.BUBBLE_PARAMS.ADAPTIVE_C}`);
  console.log(`Morphology: Kernel=${window.BUBBLE_PARAMS.MORPH_KERNEL_SIZE}, Dilate=${window.BUBBLE_PARAMS.DILATE_ITERATIONS}, Erode=${window.BUBBLE_PARAMS.ERODE_ITERATIONS}`);
  console.log(`Shape filters: Circularity>${window.BUBBLE_PARAMS.MIN_CIRCULARITY}, Aspect ratio=${window.BUBBLE_PARAMS.MIN_ASPECT_RATIO}-${window.BUBBLE_PARAMS.MAX_ASPECT_RATIO}`);
  console.log(`Text density: ${window.BUBBLE_PARAMS.MIN_TEXT_DENSITY*100}%-${window.BUBBLE_PARAMS.MAX_TEXT_DENSITY*100}%`);

  try {
    // --- STEP 1: Convert to grayscale ---
    cv.cvtColor(src, gray, cv.COLOR_RGBA2GRAY);

    // --- STEP 2: Apply adaptive thresholding ---
    cv.adaptiveThreshold(
      gray, binary, 255,
      cv.ADAPTIVE_THRESH_GAUSSIAN_C,
      cv.THRESH_BINARY_INV,
      window.BUBBLE_PARAMS.ADAPTIVE_BLOCK_SIZE,
      window.BUBBLE_PARAMS.ADAPTIVE_C
    );

    // Create visualization for binary threshold
    const binaryCanvas = document.createElement('canvas');
    binaryCanvas.id = 'debug-binary';
    cv.imshow(binaryCanvas, binary);
    const resultContainer = document.getElementById('result-container');
    if (resultContainer) {
      const debugHeader = document.createElement('h4');
      debugHeader.textContent = 'Binary Threshold (Debug)';
      resultContainer.appendChild(debugHeader);
      resultContainer.appendChild(binaryCanvas);
    }

    // --- STEP 3: Apply morphological operations ---
    kernel = cv.Mat.ones(
      window.BUBBLE_PARAMS.MORPH_KERNEL_SIZE,
      window.BUBBLE_PARAMS.MORPH_KERNEL_SIZE,
      cv.CV_8U
    );

    // Dilate to connect text within bubbles
    cv.dilate(
      binary, dilated, kernel,
      new cv.Point(-1, -1),
      window.BUBBLE_PARAMS.DILATE_ITERATIONS
    );

    // Create visualization for dilation
    const dilatedCanvas = document.createElement('canvas');
    dilatedCanvas.id = 'debug-dilated';
    cv.imshow(dilatedCanvas, dilated);
    if (resultContainer) {
      const dilatedHeader = document.createElement('h4');
      dilatedHeader.textContent = 'After Dilation (Debug)';
      resultContainer.appendChild(dilatedHeader);
      resultContainer.appendChild(dilatedCanvas);
    }

    // Erode to remove noise
    cv.erode(
      dilated, eroded, kernel,
      new cv.Point(-1, -1),
      window.BUBBLE_PARAMS.ERODE_ITERATIONS
    );

    // Create visualization for erosion
    const erodedCanvas = document.createElement('canvas');
    erodedCanvas.id = 'debug-eroded';
    cv.imshow(erodedCanvas, eroded);
    if (resultContainer) {
      const erodedHeader = document.createElement('h4');
      erodedHeader.textContent = 'After Erosion (Debug)';
      resultContainer.appendChild(erodedHeader);
      resultContainer.appendChild(erodedCanvas);
    }

    // --- STEP 4: Find contours ---
    cv.findContours(eroded, contours, hierarchy, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE);
    console.log(`Found ${contours.size()} initial contours`);

    // Create contour visualization
    const contoursVis = src.clone();
    for (let i = 0; i < contours.size(); i++) {
      const color = new cv.Scalar(
        Math.random() * 255,
        Math.random() * 255,
        Math.random() * 255,
        255
      );
      cv.drawContours(contoursVis, contours, i, color, 2);
    }

    const contoursCanvas = document.createElement('canvas');
    contoursCanvas.id = 'debug-contours';
    cv.imshow(contoursCanvas, contoursVis);
    if (resultContainer) {
      const contoursHeader = document.createElement('h4');
      contoursHeader.textContent = 'All Detected Contours (Debug)';
      resultContainer.appendChild(contoursHeader);
      resultContainer.appendChild(contoursCanvas);
    }
    contoursVis.delete();

    // --- STEP 5: Process and filter contours ---
    const bubbles = [];
    console.log("\n=== CONTOUR ANALYSIS ===");

    for (let i = 0; i < contours.size(); i++) {
      const contour = contours.get(i);

      // Get area
      let area = 0;
      try {
        area = cv.contourArea(contour);
      } catch (error) {
        console.warn(`Error calculating area for contour ${i}:`, error);
        continue;
      }

      // Skip if too small
      if (area < minArea) {
        console.log(`Contour ${i}: REJECTED - Area too small (${area.toFixed(0)}px² < ${minArea.toFixed(0)}px²)`);
        continue;
      }

      // Skip if too large
      if (area > maxArea) {
        console.log(`Contour ${i}: REJECTED - Area too large (${area.toFixed(0)}px² > ${maxArea.toFixed(0)}px²)`);
        continue;
      }

      // Calculate perimeter
      let perimeter = 0;
      try {
        perimeter = cv.arcLength(contour, true);
      } catch (error) {
        console.warn(`Error calculating perimeter for contour ${i}:`, error);
        perimeter = 1; // Avoid division by zero
      }

      // Calculate circularity
      const circularity = perimeter > 0 ? (4 * Math.PI * area) / (perimeter * perimeter) : 0;

      // Check circularity
      if (circularity < window.BUBBLE_PARAMS.MIN_CIRCULARITY) {
        console.log(`Contour ${i}: REJECTED - Circularity too low (${circularity.toFixed(3)} < ${window.BUBBLE_PARAMS.MIN_CIRCULARITY})`);
        continue;
      }

      // Get bounding rectangle
      let rect = null;
      try {
        rect = cv.boundingRect(contour);
      } catch (error) {
        console.warn(`Error calculating bounding rect for contour ${i}:`, error);
        continue;
      }

      // Calculate aspect ratio
      const aspectRatio = rect.width / rect.height;

      // Check aspect ratio
      if (aspectRatio < window.BUBBLE_PARAMS.MIN_ASPECT_RATIO || aspectRatio > window.BUBBLE_PARAMS.MAX_ASPECT_RATIO) {
        console.log(`Contour ${i}: REJECTED - Bad aspect ratio (${aspectRatio.toFixed(2)} not in range ${window.BUBBLE_PARAMS.MIN_ASPECT_RATIO}-${window.BUBBLE_PARAMS.MAX_ASPECT_RATIO})`);
        continue;
      }

      // Check text density
      let textDensity = 0;
      try {
        // Create mask for this contour
        const mask = new cv.Mat.zeros(binary.rows, binary.cols, cv.CV_8UC1);
        const textMask = new cv.Mat();

        // Draw contour on mask
        const tempContours = new cv.MatVector();
        tempContours.push_back(contour);
        cv.drawContours(mask, tempContours, 0, new cv.Scalar(255), -1);
        tempContours.delete();

        // Extract text pixels
        cv.bitwise_and(binary, mask, textMask);
        const textPixels = cv.countNonZero(textMask);
        textDensity = textPixels / area;

        // Clean up
        mask.delete();
        textMask.delete();

        // Check text density
        if (textDensity < window.BUBBLE_PARAMS.MIN_TEXT_DENSITY || textDensity > window.BUBBLE_PARAMS.MAX_TEXT_DENSITY) {
          console.log(`Contour ${i}: REJECTED - Text density out of range (${(textDensity*100).toFixed(1)}% not in ${(window.BUBBLE_PARAMS.MIN_TEXT_DENSITY*100).toFixed(1)}-${(window.BUBBLE_PARAMS.MAX_TEXT_DENSITY*100).toFixed(1)}%)`);
          continue;
        }

      } catch (error) {
        console.warn(`Error calculating text density for contour ${i}:`, error);
        continue;
      }

      // If we got here, this contour passed all filters
      console.log(`Contour ${i}: ACCEPTED - Area=${area.toFixed(0)}px², Circularity=${circularity.toFixed(3)}, ` +
                  `Aspect=${aspectRatio.toFixed(2)}, TextDensity=${(textDensity*100).toFixed(1)}%`);

      bubbles.push({
        contour: contour,
        box: rect,
        area: area,
        circularity: circularity,
        aspectRatio: aspectRatio,
        textDensity: textDensity
      });
    }

    // Sort bubbles by position
    bubbles.sort((a, b) => {
      const rowThreshold = Math.min(src.rows * 0.15, 100);
      const yDiff = a.box.y - b.box.y;

      if (Math.abs(yDiff) > rowThreshold) {
        return yDiff; // Sort by y position first (top to bottom)
      }

      return b.box.x - a.box.x; // Then sort right to left within each row
    });

    console.log(`\nDetected ${bubbles.length} speech bubbles after filtering`);

    return bubbles;

  } catch (error) {
    console.error('Error in detectSpeechBubbles:', error);
    return [];
  } finally {
    // Ensure cleanup happens even if there's an error
    gray.delete();
    binary.delete();
    dilated.delete();
    eroded.delete();
    contours.delete();
    hierarchy.delete();
    if (kernel) kernel.delete();
  }
}

// Visualize the results on the image with detailed information
function visualizeResults(src, results) {
  try {
    // Create a copy of the image for visualization
    const dst = src.clone();

    // Draw bounding boxes and add text for each bubble
    for (let i = 0; i < results.length; i++) {
      const result = results[i];
      const { x, y, width, height } = result.box;

      // Draw bounding box
      const color = new cv.Scalar(0, 255, 0, 255); // Green
      const point1 = new cv.Point(x, y);
      const point2 = new cv.Point(x + width, y + height);
      cv.rectangle(dst, point1, point2, color, 2);

      // Add bubble number and orientation (if available)
      const text = `#${i+1}${result.orientation ? ` (${result.orientation}°)` : ''}`;
      cv.putText(dst, text, new cv.Point(x, Math.max(y - 10, 15)),
                cv.FONT_HERSHEY_SIMPLEX, 0.5, new cv.Scalar(0, 0, 255, 255), 1);
    }

    // Display the result
    const canvas = document.createElement('canvas');
    canvas.id = 'output-canvas';
    cv.imshow(canvas, dst);

    // Append to result container
    const resultContainer = document.getElementById('result-container');
    if (resultContainer) {
      const heading = document.createElement('h3');
      heading.textContent = 'Detected Speech Bubbles';
      resultContainer.appendChild(heading);
      resultContainer.appendChild(canvas);

      // Add detailed information about each bubble
      if (results.length > 0) {
        const detailsTable = document.createElement('table');
        detailsTable.style.width = '100%';
        detailsTable.style.borderCollapse = 'collapse';
        detailsTable.style.marginTop = '20px';

        // Create header
        const thead = document.createElement('thead');
        thead.innerHTML = `
          <tr style="background-color: #f0f0f0; text-align: left;">
            <th style="padding: 8px; border: 1px solid #ddd;">Bubble</th>
            <th style="padding: 8px; border: 1px solid #ddd;">Position</th>
            <th style="padding: 8px; border: 1px solid #ddd;">Size</th>
            <th style="padding: 8px; border: 1px solid #ddd;">Area</th>
            <th style="padding: 8px; border: 1px solid #ddd;">Circularity</th>
            <th style="padding: 8px; border: 1px solid #ddd;">Aspect Ratio</th>
            <th style="padding: 8px; border: 1px solid #ddd;">Text Density</th>
          </tr>
        `;
        detailsTable.appendChild(thead);

        // Create body
        const tbody = document.createElement('tbody');
        results.forEach((result, i) => {
          const tr = document.createElement('tr');
          tr.style.borderBottom = '1px solid #ddd';

          tr.innerHTML = `
            <td style="padding: 8px; border: 1px solid #ddd;">#${i+1}</td>
            <td style="padding: 8px; border: 1px solid #ddd;">x=${result.box.x}, y=${result.box.y}</td>
            <td style="padding: 8px; border: 1px solid #ddd;">${result.box.width}×${result.box.height}</td>
            <td style="padding: 8px; border: 1px solid #ddd;">${result.area.toFixed(0)} px²</td>
            <td style="padding: 8px; border: 1px solid #ddd;">${result.circularity.toFixed(3)}</td>
            <td style="padding: 8px; border: 1px solid #ddd;">${result.aspectRatio ? result.aspectRatio.toFixed(2) : 'N/A'}</td>
            <td style="padding: 8px; border: 1px solid #ddd;">${result.textDensity ? (result.textDensity*100).toFixed(1) + '%' : 'N/A'}</td>
          `;

          tbody.appendChild(tr);
        });
        detailsTable.appendChild(tbody);

        resultContainer.appendChild(detailsTable);
      } else {
        // No bubbles detected message
        const noResultsDiv = document.createElement('div');
        noResultsDiv.style.padding = '20px';
        noResultsDiv.style.backgroundColor = '#fff3cd';
        noResultsDiv.style.color = '#856404';
        noResultsDiv.style.borderRadius = '4px';
        noResultsDiv.style.marginTop = '20px';
        noResultsDiv.innerHTML = `
          <h4 style="margin-top: 0;">No Speech Bubbles Detected</h4>
          <p>The algorithm didn't find any speech bubbles that match the current parameters. Try adjusting the thresholds:</p>
          <ul>
            <li>Decrease the minimum circularity (currently ${window.BUBBLE_PARAMS.MIN_CIRCULARITY})</li>
            <li>Decrease the minimum area (currently ${(window.BUBBLE_PARAMS.MIN_AREA_RATIO*100).toFixed(3)}% of image)</li>
            <li>Decrease the minimum text density (currently ${(window.BUBBLE_PARAMS.MIN_TEXT_DENSITY*100).toFixed(3)}%)</li>
          </ul>
          <p>See the debug visualizations above to understand how the image is being processed.</p>
        `;
        resultContainer.appendChild(noResultsDiv);
      }
    } else {
      console.warn('Result container not found for visualization');
    }

    // Clean up
    dst.delete();
  } catch (error) {
    console.error('Error in visualizeResults:', error);
  }
}

// Determine text orientation using Tesseract.js
async function determineTextOrientation(canvas) {
  try {
    // Try to load Tesseract if not already available
    if (typeof Tesseract === 'undefined') {
      console.warn('Tesseract not available, defaulting to horizontal orientation');
      return 0;
    }

    // First try to use Tesseract's OSD capabilities
    const worker = await Tesseract.createWorker();
    await worker.loadLanguage('osd');
    await worker.initialize('osd');

    const { data } = await worker.recognize(canvas);
    await worker.terminate();

    if (data.orientation) {
      return data.orientation.degree;
    }

    // If specific orientation data isn't available, use script detection
    if (data.script && (data.script.includes('Japanese') || data.script.includes('Chinese'))) {
      // Japanese/Chinese text might be vertical
      // Further analysis would be needed here

      // For now, analyze image aspect ratio as a simple heuristic
      if (canvas.height > canvas.width * 1.5) {
        return 90; // Likely vertical text
      }
    }

    return 0; // Default to horizontal
  } catch (error) {
    console.warn('Orientation detection failed:', error);
    return 0; // Default to horizontal
  }
}

// Perform OCR on a bubble with the given orientation
async function performOCR(canvas, orientation) {
  try {
    // Check if Tesseract is available
    if (typeof Tesseract === 'undefined') {
      console.warn('Tesseract not available for OCR');
      return { text: '', confidence: 0 };
    }

    // Create a potentially rotated canvas if needed
    let processCanvas = canvas;

    if (orientation !== 0) {
      processCanvas = document.createElement('canvas');
      const ctx = processCanvas.getContext('2d');

      // Set dimensions for the rotated canvas
      if (orientation === 90 || orientation === 270) {
        processCanvas.width = canvas.height;
        processCanvas.height = canvas.width;
      } else {
        processCanvas.width = canvas.width;
        processCanvas.height = canvas.height;
      }

      // Rotate around center
      ctx.translate(processCanvas.width / 2, processCanvas.height / 2);
      ctx.rotate((orientation * Math.PI) / 180);
      ctx.drawImage(canvas, -canvas.width / 2, -canvas.height / 2);
    }

    // Determine if text is vertical or horizontal
    const isVertical = orientation === 90 || orientation === 270;

    // Create worker and configure
    const worker = await Tesseract.createWorker();

    try {
      await worker.loadLanguage('jpn+eng');
      await worker.initialize('jpn+eng');
    } catch (error) {
      console.warn('Failed to load both languages, trying English only:', error);
      await worker.loadLanguage('eng');
      await worker.initialize('eng');
    }

    // Set page segmentation mode appropriate for text orientation
    // PSM 6: Assume a single uniform block of text
    // PSM 5: Assume a single uniform block of vertically aligned text
    await worker.setParameters({
      tessedit_pageseg_mode: isVertical ? '5' : '6',
    });

    // Perform OCR
    const result = await worker.recognize(processCanvas);
    await worker.terminate();

    return {
      text: result.data.text.trim(),
      confidence: result.data.confidence
    };
  } catch (error) {
    console.error('OCR error:', error);
    return {
      text: '',
      confidence: 0
    };
  }
}

// Helper function to update status
function updateStatus(message) {
  try {
    const statusDiv = document.getElementById('status');
    if (statusDiv) {
      statusDiv.textContent = message;
    }
    console.log(message);
  } catch (error) {
    console.warn('Error updating status:', error);
  }
}

// Set up event listeners for the UI
function setupEventListeners() {
  const fileInput = document.getElementById('file-input');
  const processButton = document.getElementById('process-button');
  const resultContainer = document.getElementById('result-container');

  if (!fileInput || !processButton) {
    console.error('Required UI elements not found.');
    return;
  }

  // Create result container if it doesn't exist
  if (!resultContainer) {
    const newContainer = document.createElement('div');
    newContainer.id = 'result-container';
    document.body.appendChild(newContainer);
    console.log('Created new result container');
  }

  processButton.addEventListener('click', () => {
    if (!fileInput.files || fileInput.files.length === 0) {
      alert('Please select an image file first.');
      return;
    }

    const file = fileInput.files[0];
    const reader = new FileReader();

    reader.onload = (e) => {
      const container = document.getElementById('result-container');
      if (container) container.innerHTML = ''; // Clear previous results

      processMangaPage(e.target.result)
        .catch(error => {
          console.error('Processing failed:', error);
          updateStatus(`Error: ${error.message}`);
        });
    };

    reader.onerror = () => {
      alert('Failed to read the selected file.');
    };

    reader.readAsDataURL(file);
  });
}

// Initialize the application
async function initApp() {
  try {
    // We'll use the existing status div from your HTML
    const statusDiv = document.getElementById('status');
    if (!statusDiv) {
      console.warn('Status div not found, might be loading before DOM is ready');
    } else {
      statusDiv.textContent = 'Initializing...';
    }

    // Both Tesseract and OpenCV are already included in your HTML
    // We just need to make sure OpenCV is fully initialized

    // Wait for OpenCV to be ready
    await ensureOpenCVReady();

    if (statusDiv) {
      statusDiv.textContent = 'Ready to process manga pages!';
    }

    // Set up UI event handlers
    setupEventListeners();

  } catch (error) {
    console.error('Initialization failed:', error);
    const statusDiv = document.getElementById('status');
    if (statusDiv) {
      statusDiv.textContent = `Failed to initialize: ${error.message}. Please refresh the page.`;
    }
  }
}

// We'll initialize when the DOM is fully loaded
document.addEventListener('DOMContentLoaded', initApp);

// Export functions for module usage
export { processMangaPage, ensureOpenCVReady as initializeOpenCV };