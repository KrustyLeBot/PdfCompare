#!/bin/bash

# Script to extract DICOM files to PNG format
# Usage: ./extract_dicom_png.sh [DICOM_FOLDER]
# If no folder is specified, defaults to "./to_extract"

# Get folder from parameter or use default
DICOM_FOLDER="${1:-./to_extract}"

# Output folder for PNG images (created in the same location as DICOM folder)
OUTPUT_FOLDER="${DICOM_FOLDER}/extracted_png"

# Check dependencies
check_dependencies() {
    local missing_deps=0
    
    if ! command -v dcmj2pnm >/dev/null 2>&1; then
        echo "Error: DCMTK (dcmj2pnm) is not installed"
        echo "Installation: sudo apt-get install dcmtk (Ubuntu/Debian) or brew install dcmtk (macOS)"
        missing_deps=1
    fi
    
    if [ $missing_deps -eq 1 ]; then
        echo ""
        echo "Alternative: ImageMagick can also be used"
        echo "Installation: sudo apt-get install imagemagick (Ubuntu/Debian) or brew install imagemagick (macOS)"
        exit 1
    fi
}

# Check dependencies
check_dependencies

# Create output folder if it doesn't exist
if [ ! -d "$OUTPUT_FOLDER" ]; then
    mkdir -p "$OUTPUT_FOLDER"
    echo "✓ Created output folder: $OUTPUT_FOLDER"
fi

# Check if the DICOM folder exists
if [ ! -d "$DICOM_FOLDER" ]; then
    echo "Error: DICOM folder not found: $DICOM_FOLDER"
    echo "Usage: $0 [DICOM_FOLDER]"
    exit 1
fi

# Get all files in the DICOM folder
file_count=$(find "$DICOM_FOLDER" -maxdepth 1 -type f ! -name "*.png" ! -name "*.txt" | wc -l)

if [ $file_count -eq 0 ]; then
    echo "No files found in: $DICOM_FOLDER"
    exit 0
fi

echo "Found $file_count file(s) in folder"
echo "Processing DICOM files from: $DICOM_FOLDER"
echo ""

success_count=0
fail_count=0

# Process each file
while IFS= read -r -d '' file; do
    filename=$(basename "$file")
    filename_no_ext="${filename%.*}"
    
    # If no extension, use the full filename
    if [ "$filename_no_ext" = "$filename" ]; then
        filename_no_ext="$filename"
    fi
    
    output_file="${OUTPUT_FOLDER}/${filename_no_ext}.png"
    
    # Try to convert using dcmj2pnm (DCMTK)
    if dcmj2pnm +oj --write-png "$file" "$output_file" 2>/dev/null; then
        if [ -f "$output_file" ]; then
            echo "  [OK] $filename -> ${filename_no_ext}.png"
            ((success_count++))
        else
            echo "  [FAIL] $filename"
            ((fail_count++))
        fi
    else
        # Try alternative method with ImageMagick if dcmj2pnm fails
        if command -v convert >/dev/null 2>&1; then
            if convert "$file" "$output_file" 2>/dev/null; then
                if [ -f "$output_file" ]; then
                    echo "  [OK] $filename -> ${filename_no_ext}.png (via ImageMagick)"
                    ((success_count++))
                else
                    echo "  [FAIL] $filename"
                    ((fail_count++))
                fi
            else
                echo "  [FAIL] $filename"
                ((fail_count++))
            fi
        else
            echo "  [FAIL] $filename"
            ((fail_count++))
        fi
    fi
done < <(find "$DICOM_FOLDER" -maxdepth 1 -type f ! -name "*.png" ! -name "*.txt" -print0)

echo ""
echo "Extraction complete!"
echo "Successfully converted: $success_count"
echo "Failed: $fail_count"
echo "Output folder: $OUTPUT_FOLDER"

# Optional: Show sample files created
if [ $success_count -gt 0 ]; then
    echo ""
    echo "Sample files created:"
    ls -lh "$OUTPUT_FOLDER" | head -5
fi
