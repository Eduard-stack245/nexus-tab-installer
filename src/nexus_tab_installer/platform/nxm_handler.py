"""Invoked by Windows when the user clicks Mod Manager Download."""
import sys
from nexus_tab_installer.nexus.nxm_inbox import submit_nxm


def main(args=None):
    args = sys.argv[1:] if args is None else args
    if len(args) != 1:
        return 2
    try:
        submit_nxm(args[0])
        return 0
    except (ValueError, OSError, Exception) as exc:
        # Do not print the NXM URL: it contains temporary account-bound tokens.
        print(f'Cannot import NXM: {type(exc).__name__}', file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())
