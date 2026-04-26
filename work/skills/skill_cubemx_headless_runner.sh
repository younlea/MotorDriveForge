#!/bin/bash
# Skill: Run STM32CubeMX in Headless mode

if [ -z "$1" ]; then
    echo "Usage: ./skill_cubemx_headless_runner.sh <path_to_ioc_file>"
    exit 1
fi

IOC_PATH=$1
SCRIPT_FILE="/tmp/generate_script.txt"

# Make the generation script
echo "config load $IOC_PATH" > $SCRIPT_FILE
echo "project generate" >> $SCRIPT_FILE
echo "exit" >> $SCRIPT_FILE

# Execute CubeMX (assuming CubeMX is in system path or standard java runtime path)
java -jar /opt/STM32CubeMX/STM32CubeMX.exe -q $SCRIPT_FILE

echo "Generation completed for $IOC_PATH."
