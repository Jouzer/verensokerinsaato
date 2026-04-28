"""Run the Simulink-style glucose model without the dashboard."""

from __future__ import annotations

import argparse

from src.simulation import SimulationInputs
from src.simulation_diagram import DiagramGlucoseControlSimulation


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Simulink-style model.")
    parser.add_argument("--minutes", type=int, default=240, help="Simulation length.")
    parser.add_argument("--print-every", type=int, default=10, help="Print interval.")
    args = parser.parse_args()

    sim = DiagramGlucoseControlSimulation()
    schedule = {
        5: SimulationInputs(carbs_g=60.0, carb_absorption_minutes=80.0),
        120: SimulationInputs(exercise_minutes=30.0, exercise_intensity=0.8),
    }

    print(sim.model_summary())
    print()
    print(
        " minute | glucose | insulin | glucagon | carb abs | carbs left | exercise "
    )
    print(
        "        |  mmol/L |   U/min |   ug/min |    g/min |          g | intensity"
    )
    print("-" * 76)

    for minute in range(args.minutes):
        inputs = schedule.get(minute, SimulationInputs())
        output = sim.step(inputs)

        is_event = inputs.carbs_g > 0.0 or inputs.exercise_minutes > 0.0
        should_print = minute == 0 or is_event or int(output.time_min) % args.print_every == 0
        if should_print:
            marker = "*" if is_event else " "
            print(
                f"{marker}{output.time_min:7.0f} |"
                f" {output.blood_glucose_mmol_l:7.2f} |"
                f" {output.insulin_u_min:7.3f} |"
                f" {output.glucagon_ug_min:8.2f} |"
                f" {output.carb_absorption_g_min:8.2f} |"
                f" {output.carbs_on_board_g:10.1f} |"
                f" {output.exercise_intensity:9.2f}"
            )

    print()
    print("* = input event at that minute")


if __name__ == "__main__":
    main()
