# Changelog

## v1.2.28
- Deduplicated zeroconf discovery by assigning a stable unique ID, enabling the ignore button.

## v1.2.27
- Expanded the README with a services/events overview and donation details.
- Added a funding link so the donation button is visible in Home Assistant.

## v1.2.25
- Handle zeroconf discovery payloads delivered as objects to avoid setup errors when reconfiguring.
- Format container list strings with spaces after commas for readability.

## v1.2.24
- Added a number box input for configuring the update interval during setup and in the options flow.
- Parallelized initial coordinator refreshes to speed up startup when multiple servers are configured.

## v1.2.10
- Dynamically create container CPU and RAM sensors when new Docker containers appear on a monitored server.
- Mark removed containers as unavailable so they disappear automatically after reloading the integration configuration.
- Resolve SSH key paths relative to the Home Assistant config directory, validate that the file exists during setup, and accept
  those relative paths in service calls.
- Document where to store SSH private keys and which path to provide when using the configuration wizard.

## v1.2.9
- Provide service translation strings across all supported languages so hassfest validation passes.
- Document the 1.2.9 release in each README to keep metadata consistent with the manifest.

## v1.2.8
- Align documentation to reference the v1.2.8 release across all languages.
- Fix the screenshots asset directory name so linked images render correctly in HACS.

## v1.2.7
- Added configurable SSH port support to the onboarding flow and stored server definitions.
- Enabled multi-server setup directly from the UI and provided an options flow to edit the list later on.
- Introduced Home Assistant diagnostics support to aid HACS quality requirements.
- Documented sudo requirements for remote actions and added release management guidance.
