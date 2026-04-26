"""Text-first glucose control simulation.

The dashboard should depend only on the public input/output dataclasses and
the GlucoseControlSimulation.step() method. That keeps this file swappable if
the final Simulink-derived model needs a different implementation later.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import exp, isfinite
import os
from pathlib import Path

import numpy as np

os.environ.setdefault(
    "MPLCONFIGDIR",
    str(Path(__file__).resolve().parents[1] / ".matplotlib-cache"),
)

try:
    import control as ct
except ModuleNotFoundError as exc:  # pragma: no cover - helpful local setup error
    raise ModuleNotFoundError(
        "Missing dependency 'control'. Install dependencies with "
        "'python -m pip install -r requirements.txt'."
    ) from exc


@dataclass(frozen=True)
class SimulationInputs:
    """Inputs the dashboard can send into one simulation step.

    Values are interpreted as new events at the current simulation minute.
    For example, carbs_g=45 means "eat 45 g now", not "45 g every minute".
    """

    carbs_g: float = 0.0
    carb_absorption_minutes: float | None = None
    exercise_minutes: float = 0.0
    exercise_intensity: float | None = None


@dataclass(frozen=True)
class SimulationOutputs:
    """Outputs the dashboard can plot after one simulation step."""

    time_min: float
    blood_glucose_mmol_l: float
    target_glucose_mmol_l: float
    insulin_u_min: float
    glucagon_ug_min: float
    carb_absorption_g_min: float
    carbs_on_board_g: float
    exercise_intensity: float
    carb_effect_mmol_l: float
    insulin_effect_mmol_l: float
    glucagon_effect_mmol_l: float
    exercise_effect_mmol_l: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class PIDSettings:
    kp: float
    ki: float
    kd: float
    output_min: float
    output_max: float
    deadband_mmol_l: float = 0.05


@dataclass(frozen=True)
class ModelSettings:
    """Default model constants.

    Time is measured in minutes. The transfer functions below are intentionally
    rough, demo-grade estimates that can be replaced with the final model.
    """

    dt_minutes: float = 1.0
    initial_glucose_mmol_l: float = 5.5
    target_glucose_mmol_l: float = 5.5
    default_carb_absorption_minutes: float = 70.0
    default_exercise_intensity: float = 0.7
    minimum_glucose_mmol_l: float = 2.0
    maximum_glucose_mmol_l: float = 18.0

    # Transfer-function gains and time constants. The variable s is 1/min.
    carb_gain_mmol_l_per_g_min: float = 2.0
    carb_tau_minutes: float = 18.0
    insulin_gain_mmol_l_per_u_min: float = -18.0
    insulin_tau_slow_minutes: float = 55.0
    insulin_tau_fast_minutes: float = 8.0
    glucagon_gain_mmol_l_per_ug_min: float = 0.035
    glucagon_tau_slow_minutes: float = 16.0
    glucagon_tau_fast_minutes: float = 4.0
    exercise_gain_mmol_l_per_intensity: float = -0.65
    exercise_tau_minutes: float = 25.0

    insulin_pid: PIDSettings = PIDSettings(
        kp=0.025,
        ki=0.00055,
        kd=0.0,
        output_min=0.0,
        output_max=0.12,
        deadband_mmol_l=0.08,
    )
    glucagon_pid: PIDSettings = PIDSettings(
        kp=8.0,
        ki=0.08,
        kd=0.0,
        output_min=0.0,
        output_max=80.0,
        deadband_mmol_l=0.08,
    )


@dataclass
class _CarbEvent:
    remaining_g: float
    absorption_minutes: float


@dataclass
class _ExerciseEvent:
    remaining_minutes: float
    intensity: float


class _TransferFunctionBlock:
    """Discrete SISO block generated from a python-control transfer function."""

    def __init__(self, name: str, continuous_tf: ct.TransferFunction, dt_minutes: float):
        self.name = name
        self.continuous_tf = continuous_tf
        self.dt_minutes = dt_minutes

        continuous_ss = ct.ss(continuous_tf)
        discrete_ss = ct.sample_system(continuous_ss, dt_minutes, method="zoh")
        self.a, self.b, self.c, self.d = [
            np.asarray(matrix, dtype=float) for matrix in ct.ssdata(discrete_ss)
        ]
        self.reset()

    def reset(self) -> None:
        self.x = np.zeros((self.a.shape[0], 1), dtype=float)

    def step(self, input_value: float) -> float:
        u = np.array([[float(input_value)]], dtype=float)
        y = self.c @ self.x + self.d @ u
        self.x = self.a @ self.x + self.b @ u
        return float(np.squeeze(y))


class _PIDBlock:
    """PID controller with output limits, kept inside the simulation model."""

    def __init__(self, settings: PIDSettings, dt_minutes: float):
        self.settings = settings
        self.dt_minutes = dt_minutes
        self.reset()

    def reset(self) -> None:
        self.integral = 0.0
        self.previous_error = 0.0
        self.output = 0.0

    def step(self, raw_error: float) -> float:
        if raw_error < self.settings.deadband_mmol_l:
            self.integral = 0.0
            self.previous_error = 0.0
            self.output = 0.0
            return self.output

        error = raw_error
        derivative = (error - self.previous_error) / self.dt_minutes
        candidate_integral = self.integral + error * self.dt_minutes

        unclamped = (
            self.settings.kp * error
            + self.settings.ki * candidate_integral
            + self.settings.kd * derivative
        )
        output = _clamp(unclamped, self.settings.output_min, self.settings.output_max)

        saturated_high = output >= self.settings.output_max and error > 0.0
        saturated_low = output <= self.settings.output_min and error < 0.0
        if not saturated_high and not saturated_low:
            self.integral = candidate_integral

        self.previous_error = error
        self.output = output
        return output


class GlucoseControlSimulation:
    """Live-step simulation backend for the dashboard and CLI demo."""

    def __init__(self, settings: ModelSettings | None = None):
        self.settings = settings or ModelSettings()
        self._validate_settings()
        self._build_transfer_functions()
        self.reset()

    def reset(self) -> SimulationOutputs:
        self.time_min = 0.0
        self.current_glucose_mmol_l = self.settings.initial_glucose_mmol_l
        self.carb_events: list[_CarbEvent] = []
        self.exercise_events: list[_ExerciseEvent] = []

        for block in self.blocks.values():
            block.reset()

        self.insulin_pid = _PIDBlock(self.settings.insulin_pid, self.settings.dt_minutes)
        self.glucagon_pid = _PIDBlock(self.settings.glucagon_pid, self.settings.dt_minutes)

        self.last_output = SimulationOutputs(
            time_min=self.time_min,
            blood_glucose_mmol_l=self.current_glucose_mmol_l,
            target_glucose_mmol_l=self.settings.target_glucose_mmol_l,
            insulin_u_min=0.0,
            glucagon_ug_min=0.0,
            carb_absorption_g_min=0.0,
            carbs_on_board_g=0.0,
            exercise_intensity=0.0,
            carb_effect_mmol_l=0.0,
            insulin_effect_mmol_l=0.0,
            glucagon_effect_mmol_l=0.0,
            exercise_effect_mmol_l=0.0,
        )
        return self.last_output

    def step(self, inputs: SimulationInputs | None = None) -> SimulationOutputs:
        inputs = inputs or SimulationInputs()
        self._add_events(inputs)

        carb_absorption_g_min = self._step_carb_events()
        exercise_intensity = self._step_exercise_events()

        insulin_error = max(0.0, self.current_glucose_mmol_l - self.settings.target_glucose_mmol_l)
        glucagon_error = max(0.0, self.settings.target_glucose_mmol_l - self.current_glucose_mmol_l)

        insulin_u_min = self.insulin_pid.step(insulin_error)
        glucagon_ug_min = self.glucagon_pid.step(glucagon_error)

        carb_effect = self.blocks["carb"].step(carb_absorption_g_min)
        insulin_effect = self.blocks["insulin"].step(insulin_u_min)
        glucagon_effect = self.blocks["glucagon"].step(glucagon_ug_min)
        exercise_effect = self.blocks["exercise"].step(exercise_intensity)

        glucose = (
            self.settings.initial_glucose_mmol_l
            + carb_effect
            + insulin_effect
            + glucagon_effect
            + exercise_effect
        )
        glucose = _clamp(
            glucose,
            self.settings.minimum_glucose_mmol_l,
            self.settings.maximum_glucose_mmol_l,
        )

        self.time_min += self.settings.dt_minutes
        self.current_glucose_mmol_l = glucose
        self.last_output = SimulationOutputs(
            time_min=self.time_min,
            blood_glucose_mmol_l=glucose,
            target_glucose_mmol_l=self.settings.target_glucose_mmol_l,
            insulin_u_min=insulin_u_min,
            glucagon_ug_min=glucagon_ug_min,
            carb_absorption_g_min=carb_absorption_g_min,
            carbs_on_board_g=sum(event.remaining_g for event in self.carb_events),
            exercise_intensity=exercise_intensity,
            carb_effect_mmol_l=carb_effect,
            insulin_effect_mmol_l=insulin_effect,
            glucagon_effect_mmol_l=glucagon_effect,
            exercise_effect_mmol_l=exercise_effect,
        )
        return self.last_output

    def model_summary(self) -> str:
        lines = [
            "Glucose control demo model",
            f"dt = {self.settings.dt_minutes:.3g} min",
            f"target glucose = {self.settings.target_glucose_mmol_l:.2f} mmol/L",
            "",
            "Transfer functions, s in 1/min:",
        ]
        for name, transfer_function in self.transfer_functions.items():
            lines.append(f"[{name}] {transfer_function}")
        lines.extend(
            [
                "",
                "Dashboard boundary:",
                "  input:  SimulationInputs",
                "  output: SimulationOutputs",
                "  step:   GlucoseControlSimulation.step(inputs)",
            ]
        )
        return "\n".join(lines)

    def _build_transfer_functions(self) -> None:
        s = ct.TransferFunction.s
        st = self.settings

        self.transfer_functions = {
            "carb": st.carb_gain_mmol_l_per_g_min / (st.carb_tau_minutes * s + 1),
            "insulin": st.insulin_gain_mmol_l_per_u_min
            / ((st.insulin_tau_slow_minutes * s + 1) * (st.insulin_tau_fast_minutes * s + 1)),
            "glucagon": st.glucagon_gain_mmol_l_per_ug_min
            / ((st.glucagon_tau_slow_minutes * s + 1) * (st.glucagon_tau_fast_minutes * s + 1)),
            "exercise": st.exercise_gain_mmol_l_per_intensity / (st.exercise_tau_minutes * s + 1),
        }
        self.blocks = {
            name: _TransferFunctionBlock(name, transfer_function, st.dt_minutes)
            for name, transfer_function in self.transfer_functions.items()
        }

    def _add_events(self, inputs: SimulationInputs) -> None:
        if inputs.carbs_g > 0.0:
            absorption_minutes = (
                inputs.carb_absorption_minutes
                if inputs.carb_absorption_minutes is not None
                else self.settings.default_carb_absorption_minutes
            )
            if absorption_minutes <= 0.0:
                raise ValueError("carb_absorption_minutes must be positive")
            self.carb_events.append(
                _CarbEvent(
                    remaining_g=float(inputs.carbs_g),
                    absorption_minutes=float(absorption_minutes),
                )
            )

        if inputs.exercise_minutes > 0.0:
            intensity = (
                inputs.exercise_intensity
                if inputs.exercise_intensity is not None
                else self.settings.default_exercise_intensity
            )
            self.exercise_events.append(
                _ExerciseEvent(
                    remaining_minutes=float(inputs.exercise_minutes),
                    intensity=max(0.0, float(intensity)),
                )
            )

    def _step_carb_events(self) -> float:
        absorption_g = 0.0
        active_events: list[_CarbEvent] = []

        for event in self.carb_events:
            absorbed_g = event.remaining_g * (
                1.0 - exp(-self.settings.dt_minutes / event.absorption_minutes)
            )
            event.remaining_g = max(0.0, event.remaining_g - absorbed_g)
            absorption_g += absorbed_g

            if event.remaining_g > 0.05:
                active_events.append(event)

        self.carb_events = active_events
        return absorption_g / self.settings.dt_minutes

    def _step_exercise_events(self) -> float:
        total_intensity = 0.0
        active_events: list[_ExerciseEvent] = []

        for event in self.exercise_events:
            if event.remaining_minutes > 0.0:
                total_intensity += event.intensity
                event.remaining_minutes -= self.settings.dt_minutes

            if event.remaining_minutes > 0.0:
                active_events.append(event)

        self.exercise_events = active_events
        return total_intensity

    def _validate_settings(self) -> None:
        numeric_values = asdict(self.settings)
        for key, value in numeric_values.items():
            if isinstance(value, dict):
                continue
            if not isinstance(value, (int, float)) or not isfinite(value):
                raise ValueError(f"Model setting {key!r} must be a finite number")

        if self.settings.dt_minutes <= 0.0:
            raise ValueError("dt_minutes must be positive")


def create_default_simulation() -> GlucoseControlSimulation:
    return GlucoseControlSimulation()


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))
