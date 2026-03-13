# SlyLED

SlyLED runs on multiple Arduino boards:
-  the Arduino Giga R1 WiFi (STM32H747, dual Cortex-M7 + M4) the parent
-  the Arduino ESP32 on a QuinLED Quad and Uno board a child
-  the Arduino ESP8266 on 6 year old QinLED Duo with a LOLIN(WEMOS) D1 R2 & mini board a child

This project consists of one parent module, typically running on the Giga board and many children modules that typically run on the ESP32/8266 boards.
Communication is via a fast wireless communication between the modules, but each module should be running a sync'd clock to the parent, and when actions are given to a child module it runs locally in sync with all the other children.

Parent Module:

Web interface with multiple tabs, DASHBOARD, SETUP, LAYOUT, ACTIONS, RUNTIME
No lights connected

Dashboard:

table of all the known children and their current status
thumbnail of any action that is currently in effect and remaining time
Stop button and a Go button

Setup:

Should list all known children in a table with key information (see child definition)
Has functions for:
- add child
- remove child
- show details
- refresh (from child)

Separate section for saving all children information to download locally and for uploading the same information, on upload and download you should be able to select which children

Should have an option to view logs, separate page, listing key information that has been polled/gathered

Support for general app settings, dark mode, units etc

Layout:

Each child needs to be graphically represented in 3d space with the attributes gathered from the child correctly interpreted, attributes from the child are how many light strings are attached and how long each string is. The direction of the strings and distances from the child node are also gathered from the child.
The parent has layout capability in the tab to move the children around relative to each other with a choice of metric or imperial units (from setup page)
This relationship between each of the children and the light strings needs to be stored in a optimized manner so that timed sequences can be created, then sent to each child for execution. The responsibility of the creation of the optimized data structure is on the parent. Child have low computer power.

Once the layout is complete, consider this the canvas of the entire project, so when actions are performed it is across the entire canvas

Actions:

These are hard coded actions that have attributes specific to the action. Initial simple actions are:

Solid: attributes are picking a colour from a colour wheel or enter a #
Flash: attributes are same as solid, but adding a time on/time off attributes
Wipe: attributes are direction (left, right, up, down), colour, speed
Off: no attributes

Runtime:

This allows to define a number of actions to be performed in sequence, so you have the ability to create a named "Runner" and add as many actions as user wants, each action that is added has a duration and an area of effect (area of effect is percentage based on the canvas with 0,0 bottom left and 100,100 upper right). Area defaults to "All", duration defaults to 5s

Multiple runners can be defined, and once saved they can be computed to optimize what needs to be send to the children, once computed there can be sync option to send to all the children. 
Only one runner can be enabled at a time.

Child module:

receives command and control from the parent. it creates a unique hostname with SLYC as a prefix and a number of fixed digits from the mac address. 

The user can create an alternate name as well as a description for this child (e.g. UPPER 1 - upper left corner strings)

it has a minimal webpage that allows for the description of how many lights are connected, how many lights, how long the string is, the type of light string, the cable direction and length from the node to the string start, and the direction the string runs.

- Module-based architecture — easy to add new patterns and hardware modules
- SPA + JSON API — single-page app polls status every 2 s; button presses use AJAX
- Two-thread Mbed RTOS design — LED animation runs on a dedicated thread, completely independent of WiFi I/O

## Quick start

1. Copy `main/arduino_secrets.h.example` to `main/arduino_secrets.h` and fill in your WiFi credentials
2. Double-press the reset button to enter bootloader mode
3. Run the build script (auto-increments the minor version on every compile):

```powershell
powershell.exe -ExecutionPolicy Bypass -File build.ps1 -Port COM7
```

4. Open `http://<board-ip>/` in a browser

## Project layout

```
main/
  main.ino          — Sketch (SPA server + LED animation)
  version.h         — APP_MAJOR / APP_MINOR (build.ps1 auto-increments minor)
  arduino_secrets.h — WiFi credentials (gitignored)
tests/
  test_web.py       — HTTP/JSON API test suite (75 tests)
docs/
  ARCHITECTURE.md   — Threading model, module design, code structure
  API.md            — Complete HTTP API reference
  HARDWARE.md       — Hardware setup, pin reference, known quirks
  PATTERNS.md       — LED pattern reference and guide for adding new patterns
build.ps1           — PowerShell build/upload script
arduino-cli.yaml    — Sets project folder as Arduino user directory
```

## Running the tests

From WSL (WSL2 cannot reach the Windows WiFi adapter directly):

```powershell
powershell.exe -Command "python -X utf8 tests/test_web.py 192.168.10.219"
```

## Documentation

See the [`docs/`](docs/) folder for full technical documentation.

## License

Private project.
