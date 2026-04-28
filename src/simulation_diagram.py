"""Simulink-diagram-inspired glucose control simulation.

This module is intentionally separate from ``simulation.py``. It keeps the same
dashboard boundary (SimulationInputs -> SimulationOutputs), but the internal
blocks mirror the Simulink diagram more closely.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import exp, isfinite
import os
from pathlib import Path

import numpy as np

from src.simulation import PIDSettings, SimulationInputs, SimulationOutputs

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
class DiagramModelSettings:
    """Settings for the Simulink-style model.

    Time is measured in minutes and s in transfer functions is 1/min.
    Saturation values are best guesses and are grouped here so they are easy to
    change or disable later.
    """

    dt_minutes: float = 1.0
    initial_glucose_mmol_l: float = 5.5
    target_glucose_mmol_l: float = 5.5
    # Keep below the dashboard's "YOU DIED" easter egg threshold (1.0 mmol/L)
    # so extreme tuning/input can trigger the overlay instead of being clipped.
    minimum_glucose_mmol_l: float = 0.0
    maximum_glucose_mmol_l: float = 40.0

    # Dashboard grams are scaled before entering the diagram's meal pulse.
    # Increase this if meals look too weak; decrease it if meals dominate.
    meal_pulse_scale: float = 1.0
    default_carb_absorption_minutes: float = 80.0

    # Exercise intensity is dimensionless. 1.0 means "fairly hard aerobic work".
    exercise_input_scale: float = 0.15
    default_exercise_intensity: float = 0.7

    # Saturation blocks. To effectively disable one, set a very wide range.
    insulin_error_max_mmol_l: float = 8.0
    glucagon_error_max_mmol_l: float = 4.0
    insulin_output_min_u_min: float = 0.0
    insulin_output_max_u_min: float = 0.025
    glucagon_output_min_ug_min: float = 0.0
    glucagon_output_max_ug_min: float = 80.0
    liver_flux_min_mmol_l_min: float = -0.10
    liver_flux_max_mmol_l_min: float = 0.10

    # PID guesses. These are deliberately conservative because this diagram has
    # long delays; aggressive integral action causes slow oscillation quickly.
    insulin_pid: PIDSettings = PIDSettings(
        kp=0.025,
        ki=0.055,
        kd=0.0,
        output_min=0.0,
        output_max=1.0,
        deadband_mmol_l=0.5,
    )
    glucagon_pid: PIDSettings = PIDSettings(
        kp=4.0,
        ki=1.0,
        kd=0.0,
        output_min=0.0,
        output_max=80.0,
        deadband_mmol_l=0.15,
    )


@dataclass
class _CarbDisplayEvent:
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

    def reset(self, steady_input: float = 0.0) -> None:
        self.x = np.zeros((self.a.shape[0], 1), dtype=float)
        if self.a.size == 0 or steady_input == 0.0:
            return

        u = np.array([[float(steady_input)]], dtype=float)
        identity = np.eye(self.a.shape[0])
        try:
            self.x = np.linalg.solve(identity - self.a, self.b @ u)
        except np.linalg.LinAlgError:
            self.x = np.zeros((self.a.shape[0], 1), dtype=float)

    def step(self, input_value: float) -> float:
        u = np.array([[float(input_value)]], dtype=float)
        y = self.c @ self.x + self.d @ u
        self.x = self.a @ self.x + self.b @ u
        return float(np.squeeze(y))


class _PIDBlock:
    """PID controller with anti-windup and output saturation."""

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
            self.reset()
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


class DiagramGlucoseControlSimulation:
    """Live-step backend based on the provided Simulink diagram."""

    def __init__(self, settings: DiagramModelSettings | None = None):
        self.settings = settings or DiagramModelSettings()
        self._validate_settings()
        self._build_blocks()
        self.reset()

    def reset(self) -> SimulationOutputs:
        st = self.settings
        self.time_min = 0.0
        self.current_glucose_mmol_l = st.initial_glucose_mmol_l
        self.pending_meal_pulse_units = 0.0
        self.carb_display_events: list[_CarbDisplayEvent] = []
        self.exercise_events: list[_ExerciseEvent] = []

        self.carb_effect_state = 0.0
        self.exercise_effect_state = 0.0
        self.insulin_effect_state = 0.0
        self.glucagon_effect_state = 0.0

        for block in self.blocks.values():
            block.reset()
        self.blocks["sensor"].reset(steady_input=st.initial_glucose_mmol_l)

        self.insulin_pid = _PIDBlock(st.insulin_pid, st.dt_minutes)
        self.glucagon_pid = _PIDBlock(st.glucagon_pid, st.dt_minutes)

        self.last_output = SimulationOutputs(
            time_min=self.time_min,
            blood_glucose_mmol_l=self.current_glucose_mmol_l,
            target_glucose_mmol_l=st.target_glucose_mmol_l,
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

        st = self.settings
        carb_absorption_g_min = self._step_carb_display_events()
        exercise_intensity = self._step_exercise_events()
        measured_glucose = self.blocks["sensor"].step(self.current_glucose_mmol_l)

        insulin_error = _clamp(
            measured_glucose - st.target_glucose_mmol_l,
            0.0,
            st.insulin_error_max_mmol_l,
        )
        glucagon_error = _clamp(
            st.target_glucose_mmol_l - measured_glucose,
            0.0,
            st.glucagon_error_max_mmol_l,
        )

        insulin_u_min = self.insulin_pid.step(insulin_error)
        glucagon_ug_min = self.glucagon_pid.step(glucagon_error)

        # Simulink saturation blocks. Change these limits in DiagramModelSettings.
        insulin_u_min = _clamp(
            insulin_u_min,
            st.insulin_output_min_u_min,
            st.insulin_output_max_u_min,
        )
        glucagon_ug_min = _clamp(
            glucagon_ug_min,
            st.glucagon_output_min_ug_min,
            st.glucagon_output_max_ug_min,
        )

        carb_flux = self._step_carb_path()
        exercise_flux = self._step_exercise_path(exercise_intensity)
        insulin_muscle_flux, insulin_liver_flux = self._step_insulin_path(insulin_u_min)
        glucagon_liver_flux = self._step_glucagon_path(glucagon_ug_min)

        liver_flux_raw = glucagon_liver_flux - insulin_liver_flux
        liver_flux = _clamp(
            liver_flux_raw,
            st.liver_flux_min_mmol_l_min,
            st.liver_flux_max_mmol_l_min,
        )

        insulin_contribution = -insulin_muscle_flux + min(0.0, liver_flux)
        glucagon_contribution = max(0.0, liver_flux)
        exercise_contribution = -exercise_flux

        self.carb_effect_state += carb_flux * st.dt_minutes
        self.exercise_effect_state += exercise_contribution * st.dt_minutes
        self.insulin_effect_state += insulin_contribution * st.dt_minutes
        self.glucagon_effect_state += glucagon_contribution * st.dt_minutes

        glucose = (
            st.initial_glucose_mmol_l
            + self.carb_effect_state
            + self.exercise_effect_state
            + self.insulin_effect_state
            + self.glucagon_effect_state
        )
        glucose = _clamp(glucose, st.minimum_glucose_mmol_l, st.maximum_glucose_mmol_l)

        self.time_min += st.dt_minutes
        self.current_glucose_mmol_l = glucose
        self.last_output = SimulationOutputs(
            time_min=self.time_min,
            blood_glucose_mmol_l=glucose,
            target_glucose_mmol_l=st.target_glucose_mmol_l,
            insulin_u_min=insulin_u_min,
            glucagon_ug_min=glucagon_ug_min,
            carb_absorption_g_min=carb_absorption_g_min,
            carbs_on_board_g=sum(event.remaining_g for event in self.carb_display_events),
            exercise_intensity=exercise_intensity,
            carb_effect_mmol_l=self.carb_effect_state,
            insulin_effect_mmol_l=self.insulin_effect_state,
            glucagon_effect_mmol_l=self.glucagon_effect_state,
            exercise_effect_mmol_l=self.exercise_effect_state,
        )
        return self.last_output

    def model_summary(self) -> str:
        lines = [
            "Simulink-style glucose control model",
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
                "Static gains:",
                f"meal_distribution = {self.meal_distribution_gain:.5g}",
                f"insulin_distribution = {self.insulin_distribution_gain:.5g}",
                f"glucagon_distribution = {self.glucagon_distribution_gain:.5g}",
                "",
                "Saturations:",
                f"insulin output = {self.settings.insulin_output_min_u_min:g}..."
                f"{self.settings.insulin_output_max_u_min:g} U/min",
                f"glucagon output = {self.settings.glucagon_output_min_ug_min:g}..."
                f"{self.settings.glucagon_output_max_ug_min:g} ug/min",
                f"liver flux = {self.settings.liver_flux_min_mmol_l_min:g}..."
                f"{self.settings.liver_flux_max_mmol_l_min:g} mmol/L/min",
            ]
        )
        return "\n".join(lines)

    def _build_blocks(self) -> None:
        s = ct.TransferFunction.s
        st = self.settings

        self.meal_distribution_gain = 5.55 / (80.0 * 0.16)
        self.insulin_distribution_gain = 1.0 / (80.0 * 0.22)
        self.glucagon_distribution_gain = 1.0 / (80.0 * 0.25)

        self.transfer_functions = {
            "meal_absorption": 1.0 / ((40.0 * s + 1.0) ** 2),
            "exercise_consumption": 0.05 / (15.0 * s + 1.0),
            "insulin_absorption": 1.0 / ((55.0 * s + 1.0) ** 2),
            "insulin_elimination": 13.0 / (13.0 * s + 1.0),
            "insulin_muscle_effect": (0.778 * 5.5) / (17.0 * s + 1.0),
            "insulin_liver_effect": 1.6 / (127.0 * s + 1.0),
            "glucagon_absorption": 1.0 / ((19.0 * s + 1.0) ** 2),
            "glucagon_elimination": 21.0 / (21.0 * s + 1.0),
            "glucagon_liver_effect": (0.0031 * 5.5) / (10.0 * s + 1.0),
            "sensor": 1.0 / (5.0 * s + 1.0),
        }
        self.blocks = {
            name: _TransferFunctionBlock(name, transfer_function, st.dt_minutes)
            for name, transfer_function in self.transfer_functions.items()
        }

    def _add_events(self, inputs: SimulationInputs) -> None:
        st = self.settings
        if inputs.carbs_g > 0.0:
            absorption_minutes = (
                inputs.carb_absorption_minutes
                if inputs.carb_absorption_minutes is not None
                else st.default_carb_absorption_minutes
            )
            if absorption_minutes <= 0.0:
                raise ValueError("carb_absorption_minutes must be positive")

            # The diagram has its own absorption transfer function. This event
            # is a one-step pulse into that model, scaled from dashboard grams.
            self.pending_meal_pulse_units += inputs.carbs_g * st.meal_pulse_scale

            # Display-only digestion estimate for the dashboard's carb graph.
            self.carb_display_events.append(
                _CarbDisplayEvent(
                    remaining_g=float(inputs.carbs_g),
                    absorption_minutes=float(absorption_minutes),
                )
            )

        if inputs.exercise_minutes > 0.0:
            intensity = (
                inputs.exercise_intensity
                if inputs.exercise_intensity is not None
                else st.default_exercise_intensity
            )
            self.exercise_events.append(
                _ExerciseEvent(
                    remaining_minutes=float(inputs.exercise_minutes),
                    intensity=max(0.0, float(intensity)),
                )
            )

    def _step_carb_display_events(self) -> float:
        absorbed_g = 0.0
        active_events: list[_CarbDisplayEvent] = []
        for event in self.carb_display_events:
            step_absorbed_g = event.remaining_g * (
                1.0 - exp(-self.settings.dt_minutes / event.absorption_minutes)
            )
            event.remaining_g = max(0.0, event.remaining_g - step_absorbed_g)
            absorbed_g += step_absorbed_g
            if event.remaining_g > 0.05:
                active_events.append(event)

        self.carb_display_events = active_events
        return absorbed_g / self.settings.dt_minutes

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

    def _step_carb_path(self) -> float:
        pulse = self.pending_meal_pulse_units / self.settings.dt_minutes
        self.pending_meal_pulse_units = 0.0
        absorbed = self.blocks["meal_absorption"].step(pulse)
        return absorbed * self.meal_distribution_gain

    def _step_exercise_path(self, exercise_intensity: float) -> float:
        exercise_input = exercise_intensity * self.settings.exercise_input_scale
        return self.blocks["exercise_consumption"].step(exercise_input)

    def _step_insulin_path(self, insulin_u_min: float) -> tuple[float, float]:
        absorbed = self.blocks["insulin_absorption"].step(insulin_u_min)
        distributed = absorbed * self.insulin_distribution_gain
        plasma = self.blocks["insulin_elimination"].step(distributed)
        muscle_flux = self.blocks["insulin_muscle_effect"].step(plasma)
        liver_flux = self.blocks["insulin_liver_effect"].step(plasma)
        return muscle_flux, liver_flux

    def _step_glucagon_path(self, glucagon_ug_min: float) -> float:
        absorbed = self.blocks["glucagon_absorption"].step(glucagon_ug_min)
        distributed = absorbed * self.glucagon_distribution_gain
        plasma = self.blocks["glucagon_elimination"].step(distributed)
        return self.blocks["glucagon_liver_effect"].step(plasma)

    def _validate_settings(self) -> None:
        for key, value in asdict(self.settings).items():
            if isinstance(value, dict):
                continue
            if not isinstance(value, (int, float)) or not isfinite(value):
                raise ValueError(f"Model setting {key!r} must be a finite number")

        if self.settings.dt_minutes <= 0.0:
            raise ValueError("dt_minutes must be positive")
        if self.settings.meal_pulse_scale < 0.0:
            raise ValueError("meal_pulse_scale must be non-negative")


def create_default_simulation() -> DiagramGlucoseControlSimulation:
    return DiagramGlucoseControlSimulation()


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))
