# Installed Wheel Runtime Acceptance

CI builds a wheel from the repository, installs it into a clean virtual environment, changes the working directory outside the source checkout, and verifies:

- canonical YAML registries are available through `valuation_engine._registry_data`;
- the exact valuation-method registry loads from the installed package;
- the Unit Contract registry loads and validates from packaged runtime resources;
- the installed `valuation-engine` console entry point starts successfully.

This gate prevents editable-install or repository-relative paths from masking missing runtime package data.
