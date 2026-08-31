"""A simple cook time remaining predictor."""

import asyncio
import math
import time
from dataclasses import dataclass
from datetime import timedelta


@dataclass
class CookingTimeData:
    """Cooking time remaining."""

    last_time: float | None = None
    remaining: timedelta | None = None
    last_food_temp: float | None = None
    last_ambient_temp: float | None = None
    linear_rate: float | None = None
    k: float | None = None


class CookingTimeEstimator:
    """A cook time remaining estimator."""

    def __init__(
        self, alpha: float = 0.15, min_rate: float = 1e-4, min_dt: float = 0.5
    ):
        """alpha: EMA smoothing factor (0.0 - 1.0).

        min_rate: Floor rate (°C/s) for linear fallback to prevent division by zero.
        min_dt: Minimum time delta (seconds) to prevent rate spikes from rapid
        jitter.
        """
        self.alpha = alpha
        self.min_rate = min_rate
        self.min_dt = min_dt
        self.state = CookingTimeData()
        self._lock = asyncio.Lock()

    def update(
        self,
        food_temp: float,
        target_temp: float,
        ambient_temp: float | None = None,
        timestamp: float | None = None,
    ) -> timedelta | None:
        """Update the estimator (for synchronous callers such as packet parsing)."""
        return self._update(food_temp, target_temp, ambient_temp, timestamp)

    async def async_update(
        self,
        food_temp: float,
        target_temp: float,
        ambient_temp: float | None = None,
        timestamp: float | None = None,
    ) -> timedelta | None:
        """Task-safe update with guards for out-of-order packets and time drift."""
        async with self._lock:
            return self._update(food_temp, target_temp, ambient_temp, timestamp)

    def reset(self) -> None:
        """Clear accumulated rate state."""
        self.state = CookingTimeData()

    def _update(
        self,
        food_temp: float,
        target_temp: float,
        ambient_temp: float | None,
        timestamp: float | None,
    ) -> timedelta | None:
        # Use monotonic clock by default to avoid NTP step adjustments
        now = timestamp if timestamp is not None else time.monotonic()

        # Guard 1: Out-of-order or duplicate telemetry packets
        if self.state.last_time is not None and now <= self.state.last_time:
            return self._calculate_eta(food_temp, target_temp, ambient_temp)

        # Guard 2: Ensure sufficient dt before recalculating rate parameters
        if self.state.last_food_temp is not None and self.state.last_time is not None:
            dt = now - self.state.last_time
            if dt >= self.min_dt:
                d_food = food_temp - self.state.last_food_temp
                instant_rate = d_food / dt

                self.state.linear_rate = (
                    instant_rate
                    if self.state.linear_rate is None
                    else (self.alpha * instant_rate)
                    + ((1 - self.alpha) * self.state.linear_rate)
                )

                ref_amb = (
                    ambient_temp
                    if ambient_temp is not None
                    else self.state.last_ambient_temp
                )
                if ref_amb is not None:
                    temp_diff = ref_amb - self.state.last_food_temp
                    if temp_diff > 1.0:
                        instant_k = max(instant_rate / temp_diff, 0.0)
                        self.state.k = (
                            instant_k
                            if self.state.k is None
                            else (self.alpha * instant_k) + ((1 - self.alpha) * self.state.k)
                        )

                self._record_state(food_temp, ambient_temp, now)
        else:
            self._record_state(food_temp, ambient_temp, now)

        return self._calculate_eta(food_temp, target_temp, ambient_temp)

    def _record_state(self, food_temp: float, ambient_temp: float | None, now: float):
        self.state.last_food_temp = food_temp
        self.state.last_ambient_temp = ambient_temp
        self.state.last_time = now

    def _calculate_eta(
        self,
        food_temp: float,
        target_temp: float,
        ambient_temp: float | None = None,
    ) -> timedelta | None:
        if food_temp >= target_temp:
            return timedelta(0)

        seconds_remaining = None

        # Newton's Law of Heating: t = -ln((T_amb - T_target) / (T_amb - T_current)) / k
        if (
            ambient_temp is not None
            and self.state.k is not None
            and self.state.k > 1e-6
            and ambient_temp > target_temp
            and ambient_temp > food_temp
        ):
            ratio = (ambient_temp - target_temp) / (ambient_temp - food_temp)
            if 0 < ratio < 1:
                seconds_remaining = -math.log(ratio) / self.state.k

        # Fallback to linear EMA speed
        if seconds_remaining is None:
            if self.state.linear_rate is None:
                return None
            delta_needed = abs(target_temp - food_temp)
            speed = max(abs(self.state.linear_rate), self.min_rate)
            seconds_remaining = delta_needed / speed

        return timedelta(seconds=round(max(0.0, seconds_remaining)))
