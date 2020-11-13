from __future__ import annotations

import inspect
import logging
import multiprocessing as mp
import shlex
import signal
import subprocess  # nosec: B404
import time
from pathlib import Path

import RPi.GPIO as GPIO  # noqa: N814
import smbus2 as smbus


class PwrCtrl(mp.Process):
    logger = logging.getLogger(f"{__name__}.{inspect.currentframe().f_code.co_name}")

    def __init__(self) -> None:
        super().__init__()
        self.daemon = True

        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        self.shutdown_pin = 4
        GPIO.setup(self.shutdown_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

        self.pulse_duration_thld = 0.30

    def __del__(self) -> None:
        self.logger.info("Cleaning up...")
        GPIO.cleanup()

    def run(self) -> int:
        """Function that waits for actuation of the fan hat's power button

        It seems that the argon fan hat sends a pulse with a duration depending on
        single (~40 ms) or double press (~20 ms). The pulse duration is detected in the
        inner while loop.

        Single long press triggers system poweroff.
        Double short press triggers system reboot.
        """
        self.logger.debug("Ignoring '[Ctrl] + C'...")
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        while True:
            GPIO.wait_for_edge(self.shutdown_pin, GPIO.RISING)

            t_pulse_0 = time.perf_counter()
            self.logger.debug("Detected rise on GPIO-pin %s", self.shutdown_pin)
            while GPIO.input(self.shutdown_pin) == GPIO.HIGH:
                time.sleep(0.01)
            t_pulse_1 = time.perf_counter()
            self.logger.debug("Detected low on GPIO-pin %s", self.shutdown_pin)

            pulse_duration = t_pulse_1 - t_pulse_0
            self.logger.debug("pulse duration = %s", pulse_duration)
            if pulse_duration <= self.pulse_duration_thld:
                self.logger.info("Rebooting")
                subprocess.run(shlex.split("shutdown -r now"))  # nosec: B603
            elif self.pulse_duration_thld < pulse_duration:
                self.logger.info("Shutting down")
                subprocess.run(shlex.split("shutdown -P now"))  # nosec: B603


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
