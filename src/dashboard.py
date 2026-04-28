"""Dash dashboard for the glucose control simulation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from math import floor
from pathlib import Path
from typing import Any

import dash
from dash import Dash, Input, Output, State, callback_context, dcc, html
import plotly.graph_objects as go

from src.simulation import PIDSettings, SimulationInputs
from src.simulation_diagram import DiagramGlucoseControlSimulation, DiagramModelSettings

# Simulation backend selection:
# Before the Simulink-style model, dashboard used:
#   from src.simulation import GlucoseControlSimulation, ModelSettings
#   ActiveSimulation = GlucoseControlSimulation
#   ActiveSettings = ModelSettings
# To restore the old demo model, switch these two aliases back.
ActiveSimulation = DiagramGlucoseControlSimulation
ActiveSettings = DiagramModelSettings


COLORS = {
    "glucose": "#d23f31",
    "insulin": "#2563eb",
    "glucagon": "#008577",
    "carbs": "#c47a00",
    "target": "#5b6472",
    "grid": "#e5e9ef",
    "text": "#1f2933",
}

MAX_HISTORY_POINTS = 8 * 60
VISIBLE_WINDOW_MINUTES = 60
DEATH_THRESHOLD_MMOL_L = 0.1


@dataclass
class RuntimeState:
    settings: ActiveSettings = field(default_factory=ActiveSettings)
    sim: ActiveSimulation = field(init=False)
    history: list[dict[str, float]] = field(default_factory=list)
    pending_inputs: deque[SimulationInputs] = field(default_factory=deque)
    messages: deque[str] = field(default_factory=lambda: deque(maxlen=6))
    minute_budget: float = 0.0
    is_dead: bool = False

    def __post_init__(self) -> None:
        self.reset(self.settings, "Simulaatio alustettu.")

    def reset(self, settings: ActiveSettings | None = None, message: str | None = None) -> None:
        if settings is not None:
            self.settings = settings
        self.sim = ActiveSimulation(self.settings)
        self.history = [self.sim.last_output.as_dict()]
        self.pending_inputs.clear()
        self.minute_budget = 0.0
        self.is_dead = False
        if message:
            self.messages.appendleft(message)


RUNTIME = RuntimeState()


def create_app() -> Dash:
    project_root = Path(__file__).resolve().parents[1]
    app = dash.Dash(
        __name__,
        assets_folder=str(project_root / "assets"),
        title="Verensokerin säätö",
        update_title=None,
        suppress_callback_exceptions=True,
    )
    app.layout = _layout()
    _register_callbacks(app)
    return app


def _layout() -> html.Main:
    settings = RUNTIME.settings
    insulin = settings.insulin_pid
    glucagon = settings.glucagon_pid

    return html.Main(
        className="app-shell",
        children=[
            dcc.Interval(id="sim-timer", interval=1000, n_intervals=0),
            html.Header(
                className="topbar",
                children=[
                    html.Div(
                        children=[
                            html.H1("Automaattinen verensokerin säätö"),
                            html.P("Kahden PID-säätimen live-demo, mmol/L"),
                        ]
                    ),
                    html.Div(
                        className="status-strip",
                        children=[
                            _metric("metric-glucose", "Verensokeri", "5.50 mmol/L"),
                            _metric("metric-insulin", "Insuliini", "0.000 U/min"),
                            _metric("metric-glucagon", "Glukagoni", "0.00 ug/min"),
                            _metric("metric-time", "Simulaatio", "0 min"),
                        ],
                    ),
                ],
            ),
            html.Section(
                className="control-grid",
                children=[
                    html.Div(
                        className="panel",
                        children=[
                            html.H2("Hiilihydraatit"),
                            html.Label("Määrä (g)", htmlFor="carbs-g"),
                            dcc.Input(id="carbs-g", type="number", value=45, min=0, step=1),
                            dcc.Checklist(
                                id="carb-options",
                                className="compact-check",
                                options=[
                                    {
                                        "label": "Näytä imeytymisaika",
                                        "value": "absorption",
                                    }
                                ],
                                value=[],
                            ),
                            html.Div(
                                id="carb-advanced",
                                className="optional-field",
                                style={"display": "none"},
                                children=[
                                    html.Label(
                                        "Imeytymisaika (min)",
                                        htmlFor="carb-absorption",
                                    ),
                                    dcc.Input(
                                        id="carb-absorption",
                                        type="number",
                                        value=settings.default_carb_absorption_minutes,
                                        min=1,
                                        step=1,
                                    ),
                                ],
                            ),
                            html.Button(
                                "Syötä hiilihydraatit",
                                id="carbs-submit",
                                className="primary-button",
                                n_clicks=0,
                            ),
                        ],
                    ),
                    html.Div(
                        className="panel",
                        children=[
                            html.H2("Aerobinen liikunta"),
                            html.Label("Kesto (min)", htmlFor="exercise-minutes"),
                            dcc.Input(
                                id="exercise-minutes",
                                type="number",
                                value=30,
                                min=0,
                                step=1,
                            ),
                            dcc.Checklist(
                                id="exercise-options",
                                className="compact-check",
                                options=[
                                    {
                                        "label": "Näytä intensiteetti",
                                        "value": "intensity",
                                    }
                                ],
                                value=[],
                            ),
                            html.Div(
                                id="exercise-advanced",
                                className="optional-field",
                                style={"display": "none"},
                                children=[
                                    html.Label("Intensiteetti", htmlFor="exercise-intensity"),
                                    dcc.Input(
                                        id="exercise-intensity",
                                        type="number",
                                        value=settings.default_exercise_intensity,
                                        min=0,
                                        step=0.1,
                                    ),
                                ],
                            ),
                            html.Button(
                                "Lisää liikunta",
                                id="exercise-submit",
                                className="primary-button",
                                n_clicks=0,
                            ),
                        ],
                    ),
                    html.Div(
                        className="panel",
                        children=[
                            html.H2("Simulaatio"),
                            html.Label("Nopeus (min / s)", htmlFor="sim-speed"),
                            dcc.Input(
                                id="sim-speed",
                                type="number",
                                value=5,
                                min=0.25,
                                max=20,
                                step=0.25,
                            ),
                            html.Div(
                                className="button-row",
                                children=[
                                    html.Button(
                                        "Pysäytä",
                                        id="pause-button",
                                        className="secondary-button",
                                        n_clicks=0,
                                    ),
                                    html.Button(
                                        "Nollaa",
                                        id="reset-button",
                                        className="secondary-button",
                                        n_clicks=0,
                                    ),
                                    html.Button(
                                        "PID-asetukset",
                                        id="open-pid",
                                        className="secondary-button",
                                        n_clicks=0,
                                    ),
                                ],
                            ),
                            html.Div(id="event-log", className="event-log"),
                        ],
                    ),
                ],
            ),
            html.Section(
                className="small-trend-grid",
                children=[
                    dcc.Graph(
                        id="glucose-trend",
                        config={"displayModeBar": False, "responsive": True},
                    ),
                    dcc.Graph(
                        id="insulin-trend",
                        config={"displayModeBar": False, "responsive": True},
                    ),
                    dcc.Graph(
                        id="glucagon-trend",
                        config={"displayModeBar": False, "responsive": True},
                    ),
                    dcc.Graph(
                        id="carb-trend",
                        config={"displayModeBar": False, "responsive": True},
                    ),
                ],
            ),
            _pid_modal(settings, insulin, glucagon),
            html.Div(
                id="death-overlay",
                className="death-overlay",
                children=[
                    html.Div(
                        className="death-panel",
                        children=[
                            html.Div("YOU DIED", className="death-title"),
                            html.Div(
                                "Verensokeri laski alle 1.0 mmol/L",
                                className="death-subtitle",
                            ),
                            html.Button(
                                "Reset",
                                id="death-reset-button",
                                className="death-reset-button",
                                n_clicks=0,
                            ),
                        ],
                    )
                ],
            ),
        ],
    )


def _metric(metric_id: str, label: str, value: str) -> html.Div:
    return html.Div(
        className="metric",
        children=[
            html.Span(label),
            html.Strong(value, id=metric_id),
        ],
    )


def _pid_modal(
    settings: ActiveSettings,
    insulin: PIDSettings,
    glucagon: PIDSettings,
) -> html.Div:
    return html.Div(
        id="pid-modal",
        className="modal",
        style={"display": "none"},
        children=[
            html.Div(
                className="modal-panel",
                children=[
                    html.Div(
                        className="modal-header",
                        children=[
                            html.H2("PID-asetukset"),
                            html.Button("Sulje", id="close-pid", className="text-button", n_clicks=0),
                        ],
                    ),
                    html.Div(
                        className="pid-grid",
                        children=[
                            _number_field(
                                "Tavoite (mmol/L)",
                                "pid-target",
                                settings.target_glucose_mmol_l,
                                step=0.1,
                            ),
                            _number_field(
                                "Insuliini max (U/min)",
                                "insulin-max",
                                insulin.output_max,
                                step=0.01,
                            ),
                            _number_field(
                                "Insuliini deadband",
                                "insulin-deadband",
                                insulin.deadband_mmol_l,
                                step=0.01,
                            ),
                            _number_field("Insuliini Kp", "insulin-kp", insulin.kp, step="any"),
                            _number_field("Insuliini Ki", "insulin-ki", insulin.ki, step="any"),
                            _number_field("Insuliini Kd", "insulin-kd", insulin.kd, step="any"),
                            _number_field("Glukagoni Kp", "glucagon-kp", glucagon.kp, step="any"),
                            _number_field("Glukagoni Ki", "glucagon-ki", glucagon.ki, step="any"),
                            _number_field("Glukagoni Kd", "glucagon-kd", glucagon.kd, step="any"),
                            _number_field(
                                "Glukagoni max (ug/min)",
                                "glucagon-max",
                                glucagon.output_max,
                                step=1,
                            ),
                            _number_field(
                                "Glukagoni deadband",
                                "glucagon-deadband",
                                glucagon.deadband_mmol_l,
                                step=0.01,
                            ),
                        ],
                    ),
                    html.Div(
                        className="modal-actions",
                        children=[
                            html.Button(
                                "Tallenna ja nollaa",
                                id="apply-pid",
                                className="primary-button",
                                n_clicks=0,
                            ),
                        ],
                    ),
                ],
            )
        ],
    )


def _number_field(label: str, input_id: str, value: float, step: float | str) -> html.Div:
    return html.Div(
        className="field",
        children=[
            html.Label(label, htmlFor=input_id),
            dcc.Input(id=input_id, type="number", value=value, step=step),
        ],
    )


def _register_callbacks(app: Dash) -> None:
    @app.callback(
        Output("carb-advanced", "style"),
        Output("exercise-advanced", "style"),
        Input("carb-options", "value"),
        Input("exercise-options", "value"),
    )
    def toggle_optional_fields(
        carb_options: list[str] | None,
        exercise_options: list[str] | None,
    ) -> tuple[dict[str, str], dict[str, str]]:
        carb_style = {"display": "block"} if "absorption" in (carb_options or []) else {"display": "none"}
        exercise_style = {"display": "block"} if "intensity" in (exercise_options or []) else {"display": "none"}
        return carb_style, exercise_style

    @app.callback(
        Output("pid-modal", "style"),
        Input("open-pid", "n_clicks"),
        Input("close-pid", "n_clicks"),
        Input("apply-pid", "n_clicks"),
    )
    def toggle_pid_modal(
        open_clicks: int,
        close_clicks: int,
        apply_clicks: int,
    ) -> dict[str, str]:
        trigger = callback_context.triggered_id
        if trigger == "open-pid":
            return {"display": "flex"}
        return {"display": "none"}

    @app.callback(
        Output("sim-timer", "disabled"),
        Output("pause-button", "children"),
        Input("pause-button", "n_clicks"),
    )
    def toggle_running(n_clicks: int) -> tuple[bool, str]:
        is_paused = bool(n_clicks and n_clicks % 2)
        return is_paused, "Jatka" if is_paused else "Pysäytä"

    @app.callback(
        Output("glucose-trend", "figure"),
        Output("insulin-trend", "figure"),
        Output("glucagon-trend", "figure"),
        Output("carb-trend", "figure"),
        Output("metric-glucose", "children"),
        Output("metric-insulin", "children"),
        Output("metric-glucagon", "children"),
        Output("metric-time", "children"),
        Output("event-log", "children"),
        Output("death-overlay", "className"),
        Input("sim-timer", "n_intervals"),
        Input("carbs-submit", "n_clicks"),
        Input("exercise-submit", "n_clicks"),
        Input("reset-button", "n_clicks"),
        Input("death-reset-button", "n_clicks"),
        Input("apply-pid", "n_clicks"),
        State("carbs-g", "value"),
        State("carb-absorption", "value"),
        State("carb-options", "value"),
        State("exercise-minutes", "value"),
        State("exercise-intensity", "value"),
        State("exercise-options", "value"),
        State("sim-speed", "value"),
        State("pid-target", "value"),
        State("insulin-kp", "value"),
        State("insulin-ki", "value"),
        State("insulin-kd", "value"),
        State("insulin-max", "value"),
        State("insulin-deadband", "value"),
        State("glucagon-kp", "value"),
        State("glucagon-ki", "value"),
        State("glucagon-kd", "value"),
        State("glucagon-max", "value"),
        State("glucagon-deadband", "value"),
    )
    def update_dashboard(
        _n_intervals: int,
        _carb_clicks: int,
        _exercise_clicks: int,
        _reset_clicks: int,
        _death_reset_clicks: int,
        _apply_clicks: int,
        carbs_g: float | None,
        carb_absorption: float | None,
        carb_options: list[str] | None,
        exercise_minutes: float | None,
        exercise_intensity: float | None,
        exercise_options: list[str] | None,
        sim_speed: float | None,
        pid_target: float | None,
        insulin_kp: float | None,
        insulin_ki: float | None,
        insulin_kd: float | None,
        insulin_max: float | None,
        insulin_deadband: float | None,
        glucagon_kp: float | None,
        glucagon_ki: float | None,
        glucagon_kd: float | None,
        glucagon_max: float | None,
        glucagon_deadband: float | None,
    ) -> tuple[Any, ...]:
        trigger = callback_context.triggered_id
        event_added = False
        should_advance = trigger == "sim-timer" and not RUNTIME.is_dead

        if trigger in {"reset-button", "death-reset-button"}:
            RUNTIME.reset(message="Simulaatio nollattu.")
        elif trigger == "apply-pid":
            settings = _settings_from_pid_values(
                pid_target,
                insulin_kp,
                insulin_ki,
                insulin_kd,
                insulin_max,
                insulin_deadband,
                glucagon_kp,
                glucagon_ki,
                glucagon_kd,
                glucagon_max,
                glucagon_deadband,
            )
            RUNTIME.reset(settings, "PID-asetukset tallennettu, simulaatio nollattu.")
        elif trigger == "carbs-submit":
            grams = _positive_or_zero(carbs_g)
            if grams > 0.0:
                absorption = (
                    _positive_or_none(carb_absorption)
                    if "absorption" in (carb_options or [])
                    else None
                )
                RUNTIME.pending_inputs.append(
                    SimulationInputs(
                        carbs_g=grams,
                        carb_absorption_minutes=absorption,
                    )
                )
                RUNTIME.messages.appendleft(f"Lisätty hiilihydraatteja {grams:g} g.")
                event_added = True
        elif trigger == "exercise-submit":
            minutes = _positive_or_zero(exercise_minutes)
            if minutes > 0.0:
                intensity = (
                    _positive_or_none(exercise_intensity)
                    if "intensity" in (exercise_options or [])
                    else None
                )
                RUNTIME.pending_inputs.append(
                    SimulationInputs(
                        exercise_minutes=minutes,
                        exercise_intensity=intensity,
                    )
                )
                RUNTIME.messages.appendleft(f"Lisätty liikuntaa {minutes:g} min.")
                event_added = True

        if event_added:
            should_advance = True

        if should_advance:
            _advance_simulation(_speed_or_default(sim_speed), force_one_step=event_added)

        if RUNTIME.history[-1]["blood_glucose_mmol_l"] < DEATH_THRESHOLD_MMOL_L:
            RUNTIME.is_dead = True

        return _dashboard_payload()


def _advance_simulation(speed_minutes_per_second: float, force_one_step: bool = False) -> None:
    RUNTIME.minute_budget += speed_minutes_per_second
    steps = floor(RUNTIME.minute_budget)
    if force_one_step:
        steps = max(1, steps)
    steps = min(steps, 120)
    RUNTIME.minute_budget -= steps

    for _ in range(steps):
        step_input = RUNTIME.pending_inputs.popleft() if RUNTIME.pending_inputs else SimulationInputs()
        output = RUNTIME.sim.step(step_input)
        RUNTIME.history.append(output.as_dict())

    if len(RUNTIME.history) > MAX_HISTORY_POINTS:
        RUNTIME.history = RUNTIME.history[-MAX_HISTORY_POINTS:]


def _dashboard_payload() -> tuple[Any, ...]:
    history = RUNTIME.history
    latest = history[-1]
    return (
        _single_figure(
            history,
            "blood_glucose_mmol_l",
            "Verensokeri",
            "mmol/L",
            COLORS["glucose"],
        ),
        _single_figure(history, "insulin_u_min", "Insuliini", "U/min", COLORS["insulin"]),
        _single_figure(
            history,
            "glucagon_ug_min",
            "Glukagoni",
            "ug/min",
            COLORS["glucagon"],
        ),
        _single_figure(
            history,
            "carbs_on_board_g",
            "Hiilihydraatit imeytymässä",
            "g",
            COLORS["carbs"],
        ),
        f"{latest['blood_glucose_mmol_l']:.2f} mmol/L",
        f"{latest['insulin_u_min']:.3f} U/min",
        f"{latest['glucagon_ug_min']:.2f} ug/min",
        f"{latest['time_min']:.0f} min",
        _event_log(),
        "death-overlay death-visible" if RUNTIME.is_dead else "death-overlay",
    )


def _single_figure(
    history: list[dict[str, float]],
    key: str,
    title: str,
    unit: str,
    color: str,
) -> go.Figure:
    x = _series(history, "time_min")
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=x,
            y=_series(history, key),
            mode="lines",
            line={"color": color, "width": 2.5},
            name=title,
        )
    )
    fig.update_layout(
        template="plotly_white",
        title={"text": title, "x": 0.03, "xanchor": "left"},
        height=230,
        margin={"l": 56, "r": 18, "t": 48, "b": 38},
        xaxis={"range": _x_range(x), "gridcolor": COLORS["grid"]},
        yaxis={
            "title": {"text": unit, "font": {"color": color}},
            "tickfont": {"color": color},
            "gridcolor": COLORS["grid"],
        },
        showlegend=False,
        font={"family": "Inter, Segoe UI, Arial, sans-serif", "color": COLORS["text"]},
    )
    return fig


def _series(history: list[dict[str, float]], key: str) -> list[float]:
    return [point[key] for point in history]


def _x_range(x_values: list[float]) -> list[float]:
    if not x_values:
        return [0, VISIBLE_WINDOW_MINUTES]
    end = max(VISIBLE_WINDOW_MINUTES, x_values[-1])
    return [end - VISIBLE_WINDOW_MINUTES, end]


def _event_log() -> list[html.Div]:
    if not RUNTIME.messages:
        return [html.Div("Ei tapahtumia vielä.")]
    return [html.Div(message) for message in RUNTIME.messages]


def _settings_from_pid_values(
    target: float | None,
    insulin_kp: float | None,
    insulin_ki: float | None,
    insulin_kd: float | None,
    insulin_max: float | None,
    insulin_deadband: float | None,
    glucagon_kp: float | None,
    glucagon_ki: float | None,
    glucagon_kd: float | None,
    glucagon_max: float | None,
    glucagon_deadband: float | None,
) -> ActiveSettings:
    current = RUNTIME.settings
    updates: dict[str, Any] = {
        "target_glucose_mmol_l": _number_or_default(
            target,
            current.target_glucose_mmol_l,
        ),
        "insulin_pid": PIDSettings(
            kp=_number_or_default(insulin_kp, current.insulin_pid.kp),
            ki=_number_or_default(insulin_ki, current.insulin_pid.ki),
            kd=_number_or_default(insulin_kd, current.insulin_pid.kd),
            output_min=0.0,
            output_max=max(0.0, _number_or_default(insulin_max, current.insulin_pid.output_max)),
            deadband_mmol_l=max(
                0.0,
                _number_or_default(insulin_deadband, current.insulin_pid.deadband_mmol_l),
            ),
        ),
        "glucagon_pid": PIDSettings(
            kp=_number_or_default(glucagon_kp, current.glucagon_pid.kp),
            ki=_number_or_default(glucagon_ki, current.glucagon_pid.ki),
            kd=_number_or_default(glucagon_kd, current.glucagon_pid.kd),
            output_min=0.0,
            output_max=max(
                0.0,
                _number_or_default(glucagon_max, current.glucagon_pid.output_max),
            ),
            deadband_mmol_l=max(
                0.0,
                _number_or_default(glucagon_deadband, current.glucagon_pid.deadband_mmol_l),
            ),
        ),
    }

    # The Simulink-style backend has extra saturation settings outside the PID
    # blocks. Keep these in sync with the modal's max fields. The original demo
    # backend does not have these attributes, so alias-based rollback still works.
    if hasattr(current, "insulin_output_max_u_min"):
        updates["insulin_output_max_u_min"] = max(
            0.0,
            _number_or_default(insulin_max, current.insulin_output_max_u_min),
        )
    if hasattr(current, "glucagon_output_max_ug_min"):
        updates["glucagon_output_max_ug_min"] = max(
            0.0,
            _number_or_default(glucagon_max, current.glucagon_output_max_ug_min),
        )

    return replace(current, **updates)


def _speed_or_default(value: float | None) -> float:
    speed = _number_or_default(value, 1.0)
    return min(20.0, max(0.25, speed))


def _positive_or_zero(value: float | None) -> float:
    return max(0.0, _number_or_default(value, 0.0))


def _positive_or_none(value: float | None) -> float | None:
    parsed = _number_or_default(value, 0.0)
    return parsed if parsed > 0.0 else None


def _number_or_default(value: float | None, default: float) -> float:
    try:
        if value is None:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)
