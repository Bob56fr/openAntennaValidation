"""Command line: ``oav <command>``."""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
import uuid
from pathlib import Path

import numpy as np

from open_antenna_validation import __version__
from open_antenna_validation.config import (
    CONFIG_FILENAME,
    ConfigError,
    default_home,
    load_config,
    save_config,
)
from open_antenna_validation.dsp.measure import Spectrum, clipping_ratio, db
from open_antenna_validation.hardware import HardwareError, create_driver
from open_antenna_validation.logging_setup import setup_logging
from open_antenna_validation.service import Station
from open_antenna_validation.web.auth import hash_password

SHUTDOWN_GRACE_S = 3


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        # utf-8-sig: Windows PowerShell 5.1 prefixes piped text with a BOM.
        return sys.stdin.buffer.readline().decode("utf-8-sig").rstrip("\r\n")
    first = getpass.getpass("Nouveau mot de passe : ")
    if getpass.getpass("Confirmation : ") != first:
        raise SystemExit("Les deux saisies diffèrent.")
    return first


def cmd_init(args: argparse.Namespace) -> int:
    home: Path = args.home
    path = home / CONFIG_FILENAME
    config = load_config(path)
    if not config.station.id:
        config.station.id = str(uuid.uuid4())
    if not config.web.session_secret:
        config.web.session_secret = secrets.token_urlsafe(32)
    if args.driver:
        config.hardware.driver = args.driver
    save_config(config, path)
    station = Station.from_home(home)
    print(f"Station {config.station.id} initialisée dans {home}")
    print(f"Base : {station.db.path} (schéma v{station.db.schema_version()})")
    station.db.close()
    if not config.web.password_hash:
        return cmd_set_password(args)
    return 0


def cmd_set_password(args: argparse.Namespace) -> int:
    path = args.home / CONFIG_FILENAME
    config = load_config(path)
    try:
        config.web.password_hash = hash_password(_read_password(args.stdin))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    save_config(config, path)
    print(f"Mot de passe enregistré dans {path}")
    print("Redémarrez le service s'il tourne, avec le même répertoire de station.")
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    config = load_config(args.home / CONFIG_FILENAME)
    driver = create_driver(config.hardware)
    availability = driver.availability()
    if not availability.available:
        print(availability.message)
        if availability.help_url:
            print(availability.help_url)
        return 2
    print(f"Pilote {driver.name} — {availability.library_version}")
    devices = driver.detect()
    for device in devices:
        inputs = ", ".join(driver.antennas(device)) or "une seule"
        print(f"  {device.model}  n° {device.serial}  entrées : {inputs}")
    if not devices:
        print("  aucun appareil détecté")
    driver.shutdown()
    return 0 if devices else 1


def cmd_probe(args: argparse.Namespace) -> int:
    """Open the device, read IQ on each input and print levels: hardware smoke test."""
    config = load_config(args.home / CONFIG_FILENAME)
    driver = create_driver(config.hardware)
    try:
        driver.set_sample_rate(config.hardware.sample_rate)
        device = driver.open(config.hardware.serial or None)
        print(f"Ouvert : {device.model} n° {device.serial}")
        driver.set_frequency(args.frequency)
        driver.set_ppm(config.hardware.ppm)
        if args.gain_step is not None:
            driver.set_gain_step(args.gain_step)
        print(f"Gain : {driver.gain_steps()[driver.get_gain_state().step_index].label}")
        rate = driver.sample_rate()
        spec = Spectrum(config.spectrum.fft_size, rate)
        print(f"{args.frequency / 1e6:.4f} MHz, {rate} S/s, bande utile "
              f"{driver.instant_bandwidth() / 1e3:.0f} kHz")
        inputs = driver.antennas(device) or [""]
        for antenna in inputs:
            driver.set_antenna(antenna)
            driver.read_iq(rate // 5)  # let the switch, the gain and the DC correction settle
            samples = driver.read_iq(int(rate * args.seconds), timeout_s=args.seconds + 2)
            power = float(np.mean(np.abs(samples) ** 2))
            peak = float(np.max(np.abs(samples)))
            frames = spec.frames(samples)
            floor = spec.noise_floor_bin(frames.mean(axis=0)) if len(frames) else 0.0
            label = f"entrée {antenna}" if antenna else "entrée unique"
            print(f"  {label} :")
            print(f"    niveau moyen      : {float(db(power)):.1f} dBFS")
            print(f"    crête             : {float(db(peak**2)):.1f} dBFS")
            print(f"    plancher par case : {float(db(floor)):.1f} dBFS "
                  f"({rate / spec.size:.0f} Hz par case)")
            print(f"    écrêtage          : {clipping_ratio(samples, 0.99) * 100:.3f} %")
        print(f"  surcharge signalée par le matériel : {'oui' if driver.overload() else 'non'}")
        print(f"  échantillons perdus : {driver.dropped_samples()}")
        return 0
    except HardwareError as exc:
        print(f"Erreur : {exc}")
        return 2
    finally:
        driver.shutdown()


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from open_antenna_validation.web import create_app

    home: Path = args.home
    config = load_config(home / CONFIG_FILENAME)
    log_path = setup_logging(home, config.logging)
    print(f"Répertoire de la station : {home}")
    if not config.web.password_hash:
        print(
            "ATTENTION : aucun mot de passe dans ce répertoire, la page refusera toute connexion."
            f" Lancez : oav --home \"{home}\" set-password"
        )
    station = Station.from_home(home)
    host = args.host or config.web.host
    port = args.port or config.web.port
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"Écoute sur {host}:{port} (réseau local). N'exposez pas cette page directement "
            "sur Internet : passez par un VPN."
        )
    print(f"Journal : {log_path}")
    print(f"Page locale : http://localhost:{port}/")
    # Open WebSockets never end by themselves: without a grace limit, Ctrl-C would wait forever
    # and a second Ctrl-C would skip the clean stop of the acquisition.
    uvicorn.run(
        create_app(station),
        host=host,
        port=port,
        log_config=None,
        timeout_graceful_shutdown=SHUTDOWN_GRACE_S,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oav", description="openAntennaValidation")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--home",
        type=Path,
        default=None,
        help="répertoire de la station (configuration, base, journaux)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    stdin_help = "lire le mot de passe sur l'entrée standard"
    p = sub.add_parser("init", help="créer la configuration et la base")
    p.add_argument("--driver", choices=["sdrplay", "simulated"])
    p.add_argument("--stdin", action="store_true", help=stdin_help)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("set-password", help="définir le mot de passe de la page web")
    p.add_argument("--stdin", action="store_true", help=stdin_help)
    p.set_defaults(func=cmd_set_password)

    p = sub.add_parser("detect", help="lister les appareils SDR")
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("probe", help="ouvrir le SDR et mesurer le niveau sur chaque entrée")
    p.add_argument("--frequency", type=float, default=7.1e6, help="Hz (défaut 7,1 MHz)")
    p.add_argument("--seconds", type=float, default=1.0)
    p.add_argument("--gain-step", type=int)
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("serve", help="lancer le service et la page web")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.home is None:
        args.home = default_home()
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
