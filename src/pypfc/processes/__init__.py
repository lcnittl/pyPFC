from __future__ import annotations

import inspect
import logging
import multiprocessing as mp
import os
import signal
import time
from pathlib import Path

import RPi.GPIO as GPIO  # noqa: N814
import smbus2 as smbus


class PwrCtrl(mp.Process):
    logger = logging.getLogger(f"{__name__}.{inspect.currentframe().f_code.co_name}")

    def __init__(self):
        super().__init__()
        self.daemon = True

        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        self.shutdown_pin = 4
        GPIO.setup(self.shutdown_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

    def __del__(self):
        self.logger.info("Cleaning up...")
        GPIO.cleanup()

    def run(self):
        self.logger.debug("Ignoring '[Ctrl] + C'...")
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        while True:
            GPIO.wait_for_edge(self.shutdown_pin, GPIO.RISING)
            self.logger.debug("Detected rise on GPIO-pin %s", self.shutdown_pin)
            t_on_press = time.perf_counter()
            time.sleep(0.2)  # Catch oscillations

            channel = GPIO.wait_for_edge(self.shutdown_pin, GPIO.FALLING, timeout=5000)
            if channel is None:
                self.logger.debug(
                    "Timeout waiting for fall on GPIO-pin %s", self.shutdown_pin
                )
            self.logger.debug("Detected fall on GPIO-pin %s", self.shutdown_pin)
            t_on_release = time.perf_counter()
            time.sleep(0.2)  # Catch oscillations

            pulsetime = t_on_release - t_on_press
            self.logger.debug("pulsetime = %s", pulsetime)
            if 1 <= pulsetime < 3:
                self.logger.info("Rebooting")
                # os.system("reboot")
            elif 3 <= pulsetime:
                self.logger.info("Shutting down")
                # os.system("shutdown now -h")


class FanCtrl(mp.Process):
    logger = logging.getLogger(f"{__name__}.{inspect.currentframe().f_code.co_name}")

    def __init__(self) -> None:
        super().__init__()
        self.daemon = True

        self.bus = smbus.SMBus(1 if GPIO.RPI_REVISION in [2, 3] else 0)
        self.address = 0x1A

        self.interval = 30

        self.fanconfig = ["65=100", "60=55", "55=10"]
        tmpconfig = self._load_config("test.cnf")
        if tmpconfig:
            self.fanconfig = tmpconfig
        self.logger.debug("fanconfig = %s", self.fanconfig)

    def __del__(self) -> None:
        self.logger.debug("Cleaning up...")
        self.bus.close()

    def run(self) -> int:
        self.logger.debug("Ignoring '[Ctrl] + C'...")
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        speed_prev = -1
        while True:
            temp = self._get_temp()
            self.logger.info("Current CPU temp: %.2f °C", temp)
            speed = self._get_fanspeed(temp)
            if speed != speed_prev:
                self._set_fan_speed(speed)
            speed_prev = speed
            time.sleep(self.interval)

    def _load_config(self, fname) -> list:
        newconfig = []
        try:
            with open(fname, "r") as file:
                for line in file:
                    if not line:
                        continue
                    tmpline = line.strip()
                    if not tmpline:
                        continue
                    if tmpline.startswith("#"):
                        continue
                    tmppair = tmpline.split("=")
                    if len(tmppair) != 2:
                        continue
                    tempval = 0
                    fanval = 0
                    try:
                        tempval = float(tmppair[0])
                        if tempval < 0 or tempval > 100:
                            continue
                    except ValueError:
                        continue
                    try:
                        fanval = int(float(tmppair[1]))
                        if fanval < 0 or fanval > 100:
                            continue
                    except ValueError:
                        continue
                    newconfig.append(f"{tempval:.2f}={fanval:d}")
            newconfig.sort(reverse=True)
        except FileNotFoundError:
            self.logger.warning("No config file found!")
            return []
        return newconfig

    def _get_temp(self) -> float:
        temp = Path("/sys/class/thermal/thermal_zone0/temp").read_text()
        temp = float(temp) * 10 ** (-3)
        return temp

    def _get_fanspeed(self, tempval) -> int:
        for curconfig in self.fanconfig:
            curpair = curconfig.split("=")
            tempcfg = float(curpair[0])
            fancfg = int(float(curpair[1]))
            if tempval >= tempcfg:
                return fancfg
        return 0

    def _set_fan_speed(self, speed) -> None:
        try:
            self.bus.write_byte(self.address, speed)
            self.logger.info("Set fan to %s %%", speed)
        except IOError:
            self.logger.error("Failed to set fan to %s %", speed)
