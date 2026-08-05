#!/bin/bash

set -e

if [ $# -ne 1 ]; then
    echo "Usage: $0 <dicom_file|zip_file>"
    exit 1
fi

INPUT_FILE="$1"

if [ ! -f "$INPUT_FILE" ]; then
    echo "Error: File not found: $INPUT_FILE"
    exit 1
fi

INPUT_DIR=$(dirname "$INPUT_FILE")
INPUT_NAME=$(basename "$INPUT_FILE")
INPUT_BASE="${INPUT_NAME%.*}"
OUTPUT_PDF="${INPUT_DIR}/${INPUT_BASE}.pdf"

TEMP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_DIR" EXIT

DICOM_DIR="$TEMP_DIR/dicoms"
FRAMES_DIR="$TEMP_DIR/frames"
mkdir -p "$DICOM_DIR" "$FRAMES_DIR"

if file "$INPUT_FILE" | grep -qi "zip"; then
    unzip -q "$INPUT_FILE" -d "$DICOM_DIR"
else
    cp "$INPUT_FILE" "$DICOM_DIR/"
fi

FRAME_INDEX=0

find "$DICOM_DIR" -type f \( -name "*.dcm" -o -name "*.DCM" -o ! -name "*.*" \) -print0 | while IFS= read -r -d '' dcm; do
    NUM_FRAMES=$(dcmdump +P NumberOfFrames "$dcm" 2>/dev/null | grep -oP '\[\K[0-9]+' || echo "1")
    
    for ((f=1; f<=NUM_FRAMES; f++)); do
        OUTPUT_FILE=$(printf "%s/frame_%06d.png" "$FRAMES_DIR" "$FRAME_INDEX")
        dcmj2pnm +on +Wm +F "$f" "$dcm" "$OUTPUT_FILE" 2>/dev/null || true
        FRAME_INDEX=$((FRAME_INDEX + 1))
    done
done

PNG_FILES=$(find "$FRAMES_DIR" -type f -name "*.png" | sort -V)

if [ -z "$PNG_FILES" ]; then
    echo "Error: No images extracted"
    exit 1
fi

if command -v img2pdf >/dev/null 2>&1; then
    echo $PNG_FILES | xargs img2pdf -o "$OUTPUT_PDF"
elif command -v convert >/dev/null 2>&1; then
    convert $PNG_FILES "$OUTPUT_PDF"
else
    echo "Error: Install img2pdf or imagemagick"
    exit 1
fi

echo "Created: $OUTPUT_PDF"