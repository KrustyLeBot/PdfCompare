#!/bin/bash

# Script to create DICOM files from PNG images
# Usage: ./create_dicom.sh [INPUT_PNG] [OUTPUT_DCM]
# If no parameters are specified, defaults to "fullbody.png" and "fullbody.dcm"

# Get parameters or use defaults
INPUT_PNG="${1:-fullbody.png}"
OUTPUT_DCM="${2:-fullbody.dcm}"

# ===== DICOM METADATA CONFIGURATION =====
# Patient information
PATIENT_NAME="fullbody"
PATIENT_AGE="066Y"
PATIENT_SEX="O"  # M=Male, F=Female, O=Other

# Study information
MODALITY="CR"  # CR=Computed Radiography, DX=Digital Radiography, MG=Mammography
BODY_PART="WHOLEBODY"
STUDY_DESCRIPTION="Full Body Scan"

# Image parameters
PHOTOMETRIC_INTERPRETATION="MONOCHROME2"  # MONOCHROME1 or MONOCHROME2
BITS_ALLOCATED=8
BITS_STORED=8
HIGH_BIT=7
# =========================================

# Check dependencies
if ! command -v convert >/dev/null 2>&1; then
    echo "Error: ImageMagick (convert) is not installed"
    echo "Installation: sudo apt-get install imagemagick (Ubuntu/Debian) or brew install imagemagick (macOS)"
    exit 1
fi

if ! command -v img2dcm >/dev/null 2>&1; then
    echo "Error: DCMTK (img2dcm) is not installed"
    echo "Installation: sudo apt-get install dcmtk (Ubuntu/Debian) or brew install dcmtk (macOS)"
    exit 1
fi

# Check input file
if [ ! -f "$INPUT_PNG" ]; then
    echo "Error: PNG file not found: $INPUT_PNG"
    echo "Usage: $0 [INPUT_PNG] [OUTPUT_DCM]"
    exit 1
fi

# Create temporary file
TEMP_RAW=$(mktemp --suffix=.raw)
TIMESTAMP=$(date +%s)

# Cleanup function
cleanup() {
    rm -f "$TEMP_RAW"
    rm -f "$TEMP_PGM" 2>/dev/null
}
trap cleanup EXIT

# Get image dimensions first
WIDTH=$(identify -format "%w" "$INPUT_PNG" 2>/dev/null)
HEIGHT=$(identify -format "%h" "$INPUT_PNG" 2>/dev/null)

if [ -z "$WIDTH" ] || [ -z "$HEIGHT" ]; then
    echo "Error: Unable to determine image dimensions"
    exit 1
fi

echo "Detected dimensions: ${WIDTH}x${HEIGHT}"

# Alternative method: Use Python with pydicom (more reliable)
TEMP_PYTHON_SCRIPT=$(mktemp --suffix=.py)

cat > "$TEMP_PYTHON_SCRIPT" << 'EOF'
import sys
import numpy as np
from PIL import Image
import pydicom
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid
import datetime
import os

def create_dicom_from_png(png_path, output_path, patient_id, patient_name, patient_age, 
                          patient_sex, modality, body_part, study_desc, 
                          photometric, bits_alloc, bits_stored, high_bit):
    # Load PNG image and convert to grayscale
    img = Image.open(png_path).convert('L')
    pixel_array = np.array(img, dtype=np.uint8)
    
    # Create DICOM dataset
    file_meta = Dataset()
    file_meta.MediaStorageSOPClassUID = '1.2.840.10008.5.1.4.1.1.1'  # CR Image Storage
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.ImplementationClassUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    
    ds = FileDataset(output_path, {}, file_meta=file_meta, preamble=b"\0" * 128)
    
    # Patient metadata
    ds.PatientName = patient_name
    ds.PatientID = patient_id
    ds.PatientAge = patient_age
    ds.PatientSex = patient_sex
    
    # Study metadata
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    ds.SOPClassUID = file_meta.MediaStorageSOPClassUID
    ds.StudyDescription = study_desc
    ds.BodyPartExamined = body_part
    
    # Dates and times
    dt = datetime.datetime.now()
    ds.StudyDate = dt.strftime('%Y%m%d')
    ds.StudyTime = dt.strftime('%H%M%S')
    ds.SeriesDate = dt.strftime('%Y%m%d')
    ds.SeriesTime = dt.strftime('%H%M%S')
    ds.ContentDate = dt.strftime('%Y%m%d')
    ds.ContentTime = dt.strftime('%H%M%S')
    
    # Image metadata
    ds.Modality = modality
    ds.Rows = pixel_array.shape[0]
    ds.Columns = pixel_array.shape[1]
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = photometric
    ds.BitsAllocated = int(bits_alloc)
    ds.BitsStored = int(bits_stored)
    ds.HighBit = int(high_bit)
    ds.PixelRepresentation = 0
    
    # Image data
    ds.PixelData = pixel_array.tobytes()
    
    # Save
    ds.save_as(output_path, write_like_original=False)
    return True

if __name__ == "__main__":
    if len(sys.argv) != 14:
        print("Usage: script.py <input_png> <output_dcm> <patient_id> <patient_name> <patient_age> <patient_sex> <modality> <body_part> <study_desc> <photometric> <bits_alloc> <bits_stored> <high_bit>")
        sys.exit(1)
    
    try:
        success = create_dicom_from_png(
            sys.argv[1],   # png_path
            sys.argv[2],   # output_path
            sys.argv[3],   # patient_id
            sys.argv[4],   # patient_name
            sys.argv[5],   # patient_age
            sys.argv[6],   # patient_sex
            sys.argv[7],   # modality
            sys.argv[8],   # body_part
            sys.argv[9],   # study_desc
            sys.argv[10],  # photometric
            sys.argv[11],  # bits_alloc
            sys.argv[12],  # bits_stored
            sys.argv[13]   # high_bit
        )
        if success:
            print("✓ DICOM created successfully via Python")
        else:
            print("Error creating DICOM")
            sys.exit(1)
    except Exception as e:
        print(f"Python error: {e}")
        sys.exit(1)
EOF

echo "Attempting to create DICOM with Python/pydicom..."

# Check if Python and dependencies are available
if command -v python3 >/dev/null 2>&1; then
    if python3 -c "import pydicom, PIL, numpy" >/dev/null 2>&1; then
        if python3 "$TEMP_PYTHON_SCRIPT" "$INPUT_PNG" "$OUTPUT_DCM" "PNG_${TIMESTAMP}" \
            "$PATIENT_NAME" "$PATIENT_AGE" "$PATIENT_SEX" "$MODALITY" "$BODY_PART" \
            "$STUDY_DESCRIPTION" "$PHOTOMETRIC_INTERPRETATION" "$BITS_ALLOCATED" \
            "$BITS_STORED" "$HIGH_BIT"; then
            rm -f "$TEMP_PYTHON_SCRIPT"
            echo "✓ Successfully created with Python"
        else
            echo "Python method failed, attempting simplified approach..."
            rm -f "$TEMP_PYTHON_SCRIPT"
            exit 1
        fi
    else
        echo "Missing Python dependencies. Installation:"
        echo "pip install pydicom pillow numpy"
        
        # Fallback method with dcmconv if available
        echo "Attempting fallback method..."
        TEMP_BMP=$(mktemp --suffix=.bmp)
        if convert "$INPUT_PNG" -depth 8 -colorspace Gray "$TEMP_BMP"; then
            if command -v dcmconv >/dev/null 2>&1; then
                dcmconv --write-dataset "$TEMP_BMP" "$OUTPUT_DCM" || exit 1
            else
                echo "Unable to create DICOM. Please install Python dependencies."
                exit 1
            fi
        fi
        rm -f "$TEMP_BMP"
    fi
else
    echo "Python3 not found"
    exit 1
fi

# Verify that the DICOM file was created
if [ ! -f "$OUTPUT_DCM" ]; then
    echo "Error: DICOM file was not created"
    exit 1
fi

echo "✓ DICOM created successfully: $OUTPUT_DCM"
echo "File size: $(du -h "$OUTPUT_DCM" | cut -f1)"

# Optional: Verification with dcmdump if available
if command -v dcmdump >/dev/null 2>&1; then
    echo "DICOM file verification:"
    dcmdump --print-short "$OUTPUT_DCM" | head -10
fi