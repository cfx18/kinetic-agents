"""Observable-aligned Cantera adapters for the v0.6 development catalog.

These adapters intentionally follow the *declared measurement definition*.
They are development infrastructure: a successful solve means that a catalog
row can be simulated reproducibly, not that the mechanism agrees with the
experiment or that the row is admitted to a frozen benchmark.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from time import perf_counter
from typing import Mapping


def validate_mixture(temperature, pressure, mole_fractions):
    """Reject invalid inputs before Cantera can normalize/clip composition."""
    import math
    if (not math.isfinite(temperature) or temperature <= 0
            or not math.isfinite(pressure) or pressure <= 0
            or not isinstance(mole_fractions, Mapping) or not mole_fractions
            or any(not isinstance(s, str) or not s or not math.isfinite(v) or v < 0
                   for s, v in mole_fractions.items()) or sum(mole_fractions.values()) <= 0):
        raise ValueError('finite positive T/P and nonnegative nonempty mixture required')


def checked_flame_speed(flame):
    import numpy as np
    speed = float(flame.velocity[0])
    if (not np.isfinite(speed) or speed <= 0 or not np.all(np.isfinite(flame.T))
            or np.any(flame.T <= 0) or not np.all(np.isfinite(flame.Y))
            or len(flame.grid) < 2 or not np.all(np.diff(flame.grid) > 0)):
        raise RuntimeError('invalid flame output; solver return does not certify physical validity')
    return speed


@dataclass(frozen=True)
class ObservablePrediction:
    value: float
    unit: str
    adapter: str
    mechanism: str
    definition: str
    solver_version: str
    wall_time_s: float
    diagnostics: dict[str, float | int | str]

    def as_dict(self) -> dict:
        return asdict(self)


def constant_volume_idt_max_dpdt(
    mechanism: str,
    *,
    temperature_k: float,
    pressure_pa: float,
    mole_fractions: Mapping[str, float],
    t_end_s: float,
    sample_count: int = 2500,
) -> ObservablePrediction:
    """Return constant-volume ignition delay using time of maximum dP/dt.

    A fixed output grid makes the definition independent of the solver's
    adaptive internal step locations. The adapter fails closed if no material
    ignition temperature rise is observed or the maximum lies at a boundary.
    """
    import math
    validate_mixture(temperature_k, pressure_pa, mole_fractions)
    if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < 100:
        raise ValueError("sample_count must be at least 100")
    if not math.isfinite(t_end_s) or t_end_s <= 0:
        raise ValueError("t_end_s must be positive")

    import cantera as ct
    import numpy as np

    started = perf_counter()
    gas = ct.Solution(mechanism)
    gas.TPX = float(temperature_k), float(pressure_pa), dict(mole_fractions)
    reactor = ct.IdealGasReactor(gas, energy="on")
    network = ct.ReactorNet([reactor])
    network.atol = 1e-12
    network.rtol = 1e-8

    times = np.linspace(0.0, float(t_end_s), int(sample_count) + 1)
    pressures = np.empty_like(times)
    temperatures = np.empty_like(times)
    pressures[0] = reactor.thermo.P
    temperatures[0] = reactor.T
    for index, time_s in enumerate(times[1:], start=1):
        network.advance(float(time_s))
        pressures[index] = reactor.thermo.P
        temperatures[index] = reactor.T

    dpdt = np.gradient(pressures, times, edge_order=2)
    if not all(np.all(np.isfinite(v)) for v in (pressures, temperatures, dpdt)):
        raise RuntimeError('nonfinite ignition trace')
    peak_index = int(np.argmax(dpdt))
    temperature_rise = float(temperatures.max() - temperatures[0])
    if temperature_rise < 50.0:
        raise RuntimeError(
            f"no resolved ignition: maximum temperature rise is {temperature_rise:.3g} K"
        )
    if peak_index in {0, len(times) - 1}:
        raise RuntimeError("maximum pressure-rise rate lies at integration boundary")

    return ObservablePrediction(
        value=float(times[peak_index]),
        unit="s",
        adapter="cantera_constant_volume_shock_tube",
        mechanism=mechanism,
        definition="time to maximum pressure-rise rate",
        solver_version=ct.__version__,
        wall_time_s=float(perf_counter() - started),
        diagnostics={
            "t_end_s": float(t_end_s),
            "sample_count": int(sample_count),
            "time_resolution_s": float(times[1] - times[0]),
            "peak_dpdt_pa_per_s": float(dpdt[peak_index]),
            "temperature_rise_k": temperature_rise,
        },
    )


def premixed_laminar_flame_speed(
    mechanism: str,
    *,
    fuel: str,
    equivalence_ratio: float,
    unburned_temperature_k: float,
    pressure_pa: float,
    oxidizer: str = "O2:1, N2:3.76",
    width_m: float = 0.03,
) -> ObservablePrediction:
    """Return an unstretched freely propagating premixed flame speed."""
    import cantera as ct
    import math
    if not all(math.isfinite(v) and v > 0 for v in
               (equivalence_ratio, unburned_temperature_k, pressure_pa, width_m)):
        raise ValueError('finite positive phi/T/P/width required')

    started = perf_counter()
    gas = ct.Solution(mechanism)
    gas.set_equivalence_ratio(float(equivalence_ratio), fuel, oxidizer)
    gas.TP = float(unburned_temperature_k), float(pressure_pa)
    flame = ct.FreeFlame(gas, width=float(width_m))
    flame.transport_model = "mixture-averaged"
    flame.set_refine_criteria(ratio=4.0, slope=0.1, curve=0.1, prune=0.02)
    flame.solve(loglevel=0, auto=True)
    return ObservablePrediction(
        value=checked_flame_speed(flame),
        unit="m/s",
        adapter="cantera_free_flame",
        mechanism=mechanism,
        definition="unstretched freely propagating laminar flame speed",
        solver_version=ct.__version__,
        wall_time_s=float(perf_counter() - started),
        diagnostics={
            "grid_points": int(len(flame.grid)),
            "width_m": float(width_m),
            "transport_model": str(flame.transport_model),
            "maximum_temperature_k": float(max(flame.T)),
        },
    )


def premixed_laminar_flame_speed_composition(
    mechanism: str,
    *,
    mole_fractions: Mapping[str, float],
    unburned_temperature_k: float,
    pressure_pa: float,
    width_m: float = 0.03,
) -> ObservablePrediction:
    """Return flame speed for an explicitly reported experimental mixture.

    This avoids reconstructing oxidizer composition from equivalence ratio,
    which can silently erase helium, argon, water, or non-air dilution.
    """
    import cantera as ct
    import math
    validate_mixture(unburned_temperature_k, pressure_pa, mole_fractions)
    if not math.isfinite(width_m) or width_m <= 0:
        raise ValueError('finite positive flame width required')

    started = perf_counter()
    gas = ct.Solution(mechanism)
    gas.TPX = (
        float(unburned_temperature_k),
        float(pressure_pa),
        dict(mole_fractions),
    )
    flame = ct.FreeFlame(gas, width=float(width_m))
    flame.transport_model = "mixture-averaged"
    flame.set_refine_criteria(ratio=4.0, slope=0.1, curve=0.1, prune=0.02)
    flame.solve(loglevel=0, auto=True)
    return ObservablePrediction(
        value=checked_flame_speed(flame),
        unit="m/s",
        adapter="cantera_free_flame_explicit_composition",
        mechanism=mechanism,
        definition="unstretched freely propagating laminar flame speed",
        solver_version=ct.__version__,
        wall_time_s=float(perf_counter() - started),
        diagnostics={
            "grid_points": int(len(flame.grid)),
            "width_m": float(width_m),
            "transport_model": str(flame.transport_model),
            "maximum_temperature_k": float(max(flame.T)),
            "reported_mole_fraction_sum": float(sum(mole_fractions.values())),
        },
    )
