from __future__ import annotations

import inspect
import logging
import multiprocessing as mp
import shlex
import signal
import subprocess  # nosec: B404
import time
from pathlib import Path
from typing import Optional

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

        self.pulse_interval = 0.01
        self.pulse_duration_thld = 0.030

    def run(self) -> int:
        """Function that waits for actuation of the fan hat's power button

        It seems that the argon fan hat sends a pulse with a duration depending on
        single (~40 ms) or double press (~20 ms). The pulse duration is detected in the
        inner while loop.

        Single long press triggers system poweroff.
        Double short press triggers system reboot.
        """
        self.logger.debug("Ignoring SIGINT...")
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        while True:
            GPIO.wait_for_edge(self.shutdown_pin, GPIO.RISING)

            t_pulse_0 = time.perf_counter()
            self.logger.debug("Detected rise on GPIO-pin %s", self.shutdown_pin)
            GPIO.wait_for_edge(self.shutdown_pin, GPIO.FALLING)

            t_pulse_1 = time.perf_counter()
            self.logger.debug("Detected fall on GPIO-pin %s", self.shutdown_pin)

            pulse_duration = t_pulse_1 - t_pulse_0
            self.logger.debug("pulse duration = %s", pulse_duration)
            if pulse_duration <= self.pulse_duration_thld:
                self.logger.info("Rebooting")
                subprocess.run(shlex.split("shutdown -r now"))  # nosec: B603
            elif self.pulse_duration_thld < pulse_duration:
                self.logger.info("Shutting down")
                subprocess.run(shlex.split("shutdown -P now"))  # nosec: B603

    def terminate(self) -> None:
        self.logger.debug("Terminating...")
        self._cleanup()

        super().terminate()

    def kill(self) -> None:
        self.logger.debug("Killing...")
        self._cleanup()

        super().terminate()

    def _cleanup(self) -> None:
        self.logger.debug("Cleaning up...")
        GPIO.cleanup()


class FanCtrl(mp.Process):
    logger = logging.getLogger(f"{__name__}.{inspect.currentframe().f_code.co_name}")

    def __init__(self) -> None:
        super().__init__()
        self.daemon = True

        self.bus = smbus.SMBus(1 if GPIO.RPI_REVISION in [2, 3] else 0)
        self.address = 0x1A

        self.interval = 30

        self.fan_tests = [(25, 1), (50, 1), (75, 1), (100, 2), (5, 2)]
        self.temp_fanspeed_map = {55.0: 10, 60.0: 55, 65.0: 100}
        temp_fanspeed_map = self._load_config("test.cnf")
        if temp_fanspeed_map:
            self.temp_fanspeed_map = temp_fanspeed_map
        self.logger.debug("temp_fanspeed_map = %s", self.temp_fanspeed_map)

    def run(self) -> int:
        self.logger.debug("Ignoring SIGINT...")
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        self._run_fan_test(self.fan_tests)

        speed_prev = -1
        while True:
            temp = self._read_temp()
            self.logger.debug("Current CPU temp: %.2f °C", temp)
            speed = self._temp_to_fanspeed(temp)
            if speed != speed_prev:
                self._apply_fanspeed(speed)
            speed_prev = speed
            time.sleep(self.interval)

    def terminate(self) -> None:
        self.logger.debug("Terminating...")
        self._cleanup()

        super().terminate()

    def kill(self) -> None:
        self.logger.debug("Killing...")
        self._cleanup()

        super().terminate()

    def _cleanup(self) -> None:
        self.logger.debug("Cleaning up...")
        self._apply_fanspeed(0)
        self.bus.close()

    def _load_config(self, fname: str) -> dict:
        temp_fanspeed_map = {}
        try:
            with open(fname) as file:
                for line in file:
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("#"):
                        continue
                    temp_fanspeed_pair = line.split("=")
                    if len(temp_fanspeed_pair) != 2:
                        continue
                    try:
                        temp = float(temp_fanspeed_pair[0])
                        if not 0 <= temp <= 100:
                            continue
                        fanspeed = int(float(temp_fanspeed_pair[1]))
                        if not 0 <= fanspeed <= 100:
                            continue
                    except ValueError:
                        continue
                    temp_fanspeed_map[temp] = fanspeed
            # Sorting is not necessary at this point, yet, it is nicer for the
            # user to check the values
            temp_fanspeed_map = {
                key: val for key, val in sorted(temp_fanspeed_map.items())
            }
        except FileNotFoundError:
            self.logger.warning("No config file found!")
        return temp_fanspeed_map

    def _run_fan_test(self, tests: list[tuple[int, float]] | None = None) -> None:
        self.logger.debug("Testing fan...")
        if not tests:
            self.logger.debug("No tests defined")
            return
        for test in tests:
            self._apply_fanspeed(test[0])
            time.sleep(test[1])

    def _read_temp(self) -> float:
        temp = Path("/sys/class/thermal/thermal_zone0/temp").read_text()
        temp = float(temp) * 10 ** (-3)
        return temp

    def _temp_to_fanspeed(self, temp_current: float) -> int:
        for temp in sorted(self.temp_fanspeed_map):
            if temp_current < temp:
                continue
            return self.temp_fanspeed_map[temp]
        return 0

    def _apply_fanspeed(self, speed: int) -> None:
        try:
            self.bus.write_byte(self.address, speed)
            self.logger.info("Set fan to %s %%", speed)
        except OSError:
            self.logger.error("Failed to set fan to %s %%", speed)
