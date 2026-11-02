import argparse
import os
from pathlib import Path

from service import Service


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    env = dict(os.environ, ONBOARDING_ENABLED='1')
    service = Service(args.state_dir, env)
    service.web.check(require_events=not args.prepare)
    print('Public connection configuration verified.' if not args.prepare else 'App identity and bot identity verified. Webhook activation is still required.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Public setup check failed:', type(error).__name__, str(error))
        raise SystemExit(1)
