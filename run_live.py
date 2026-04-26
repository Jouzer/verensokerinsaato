"""Run the glucose model in a terminal live mode.

Controls:
  h = add carbohydrates
  l = add aerobic exercise
  q = quit
"""

from __future__ import annotations

import argparse
import time
from collections import deque
from typing import Deque

from src.simulation import GlucoseControlSimulation, SimulationInputs

try:
    import msvcrt
except ImportError:  # pragma: no cover - Windows is the target environment here
    msvcrt = None


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the live terminal simulation.")
    parser.add_argument(
        "--tick-seconds",
        type=float,
        default=1.0,
        help="Real seconds between simulation steps. Default: 1 s = 1 min.",
    )
    parser.add_argument(
        "--max-ticks",
        type=int,
        default=None,
        help="Optional stop after N ticks, useful for smoke tests.",
    )
    parser.add_argument(
        "--exercise-at",
        type=int,
        default=None,
        help="Optional simulated minute when an exercise event is added.",
    )
    parser.add_argument(
        "--exercise-minutes",
        type=float,
        default=30.0,
        help="Duration for --exercise-at.",
    )
    parser.add_argument(
        "--exercise-intensity",
        type=float,
        default=0.7,
        help="Intensity for --exercise-at.",
    )
    args = parser.parse_args()

    if args.tick_seconds <= 0.0:
        raise ValueError("--tick-seconds must be positive")

    sim = GlucoseControlSimulation()
    pending_inputs: Deque[SimulationInputs] = deque()
    previous = sim.last_output

    print(sim.model_summary())
    print()
    print("Live controls: h = hiilihydraatti, l = liikunta, q = quit")
    print("Default speed: 1 real second = 1 simulated minute")
    print()
    print(" minute | glucose | delta | insulin | glucagon | carb abs | carbs left | exercise")
    print("        |  mmol/L | mmol/L |   U/min |   ug/min |    g/min |          g | intensity")
    print("-" * 86)

    ticks = 0
    try:
        while args.max_ticks is None or ticks < args.max_ticks:
            started_at = time.monotonic()

            command = _read_keypress()
            if command == "q":
                print("\nStopped.")
                break
            if command == "h":
                pending_inputs.append(_ask_carbs())
            elif command == "l":
                pending_inputs.append(_ask_exercise())

            if (
                args.exercise_at is not None
                and int(sim.last_output.time_min) == args.exercise_at
            ):
                pending_inputs.append(
                    SimulationInputs(
                        exercise_minutes=args.exercise_minutes,
                        exercise_intensity=args.exercise_intensity,
                    )
                )
                args.exercise_at = None

            inputs = pending_inputs.popleft() if pending_inputs else SimulationInputs()
            output = sim.step(inputs)
            ticks += 1

            marker = "*" if _has_event(inputs) else " "
            print(
                f"{marker}{output.time_min:7.0f} |"
                f" {output.blood_glucose_mmol_l:7.2f} |"
                f" {output.blood_glucose_mmol_l - previous.blood_glucose_mmol_l:5.2f} |"
                f" {output.insulin_u_min:7.3f} |"
                f" {output.glucagon_ug_min:8.2f} |"
                f" {output.carb_absorption_g_min:8.2f} |"
                f" {output.carbs_on_board_g:10.1f} |"
                f" {output.exercise_intensity:9.2f}",
                flush=True,
            )
            previous = output

            elapsed = time.monotonic() - started_at
            time.sleep(max(0.0, args.tick_seconds - elapsed))
    except KeyboardInterrupt:
        print("\nStopped.")


def _read_keypress() -> str | None:
    if msvcrt is None:
        return None

    if not msvcrt.kbhit():
        return None

    key = msvcrt.getwch().lower()
    if key in {"h", "l", "q"}:
        return key

    return None


def _ask_carbs() -> SimulationInputs:
    grams = _ask_positive_float("Monta grammaa hiilihydraattia haluat lisata? ")
    absorption = _ask_optional_positive_float(
        "Imeytymisaika minuutteina, Enter = oletus: "
    )
    print(f"Lisataan {grams:g} g hiilihydraattia seuraavalle simulaatioaskeleelle.")
    return SimulationInputs(carbs_g=grams, carb_absorption_minutes=absorption)


def _ask_exercise() -> SimulationInputs:
    minutes = _ask_positive_float("Monta minuuttia aerobista liikuntaa haluat lisata? ")
    intensity = _ask_optional_positive_float(
        "Intensiteetti, esim. 0.5 kevyt / 1.0 raskas, Enter = oletus: "
    )
    print(f"Lisataan {minutes:g} min liikuntaa seuraavalle simulaatioaskeleelle.")
    return SimulationInputs(exercise_minutes=minutes, exercise_intensity=intensity)


def _ask_positive_float(prompt: str) -> float:
    while True:
        raw_value = input(prompt).strip().replace(",", ".")
        try:
            value = float(raw_value)
        except ValueError:
            print("Anna numeroarvo.")
            continue

        if value <= 0.0:
            print("Arvon taytyy olla positiivinen.")
            continue

        return value


def _ask_optional_positive_float(prompt: str) -> float | None:
    while True:
        raw_value = input(prompt).strip().replace(",", ".")
        if raw_value == "":
            return None

        try:
            value = float(raw_value)
        except ValueError:
            print("Anna numeroarvo tai paina Enter.")
            continue

        if value <= 0.0:
            print("Arvon taytyy olla positiivinen.")
            continue

        return value


def _has_event(inputs: SimulationInputs) -> bool:
    return inputs.carbs_g > 0.0 or inputs.exercise_minutes > 0.0


if __name__ == "__main__":
    main()
