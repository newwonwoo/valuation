# Platform residual acceptance checklist

- Wheel built by CI
- Installed into a clean virtual environment
- Validation executed outside repository checkout
- Packaged canonical YAML registries load through importlib resources
- Unit Contract and exact method registries validate from installed wheel
- Installed console entry point starts
- Existing regression/performance/PM gates remain green
