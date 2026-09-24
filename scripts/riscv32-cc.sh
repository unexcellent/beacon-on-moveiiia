#!/bin/bash
# Wrapper around riscv32-esp-elf-gcc for the riscv32imafc-esp-espidf target.
# Adds the picolibc include path (provides <endian.h>) and the correct
# march/mabi flags matching the ESP32-P4 ABI used by the rest of the firmware.
# Flags go AFTER $@ so they override cc-crate's -march=rv32imafc -mabi=ilp32.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
TOOLCHAIN="$PROJECT_DIR/.embuild/espressif/tools/riscv32-esp-elf/esp-14.2.0_20251107/riscv32-esp-elf"
exec "$TOOLCHAIN/bin/riscv32-esp-elf-gcc" \
    "-I$TOOLCHAIN/picolibc/include" \
    "$@" \
    "-march=rv32imafc_zicsr_zifencei" \
    "-mabi=ilp32f" \
    "-fno-PIC"
