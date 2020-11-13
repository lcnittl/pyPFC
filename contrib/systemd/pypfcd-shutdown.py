#!/usr/bin/env python3
import sys

import RPi.GPIO as GPIO  # noqa: N814
import smbus2 as smbus

address = 0x1A

if len(sys.argv) > 1:
    with smbus.SMBus(1 if GPIO.RPI_REVISION in [2, 3] else 0) as bus:
        bus.write_byte(address, 0)
        if sys.argv[1] in ["poweroff", "halt"]:
            try:
                bus.write_byte_data(address, 0, 0xFF)
            except Exception:
                rev = 0
