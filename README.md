# README.md

## Using the GO2 HTTP API

### Installing Poetry

We'll use Poetry to build our python module. All dependencies are defined in the pyproject.toml file. 

Follow the instructions here to install poetry:
https://python-poetry.org/docs/#installation

Once the installation is verified we can install the module

### Installing the Module

- Navigate to the root of the repo and run `poetry install`. This should sort out all dependencies. It uses `pip` under the hood.

- All installations happen in a virtual environment. You can use [poetry env](https://python-poetry.org/docs/cli/#env) commands to access it. To activate the virtual env, run:
  - linux / terminal - `eval $(poetry env activate)` 
  - windows / powershell - `Invoke-Expression (poetry env activate)`

### Connecting to the Robot

To send commands to the robot, you need to be on the same LAN as the Raspberry PI associated with the robot. 

#### When you're on the same LAN

One way is actually be on the same LAN by connecting your machine to the router that the PI is connected to.
Once the connection is established, make sure you can ping it or ssh into it

#### When you're not on the same LAN

If you're not on the same room, you can virtually be on the same LAN via Tailscale. 

_Instructions coming soon_

### Sending Commands Programmatically

Use the `ArcanaGO2` class with the correct address and URL to send commands to the server listening on the Raspberry PI. Start with safe commands like `sit` or `move` with 0 velocity to ensure you have a connection. 

See the `scripts` folder for examples.

### Joystick Control

We'll launch a simple GUI to send various commands to the robot interactively. This will mimic the remote controller. 

_Instructions coming soon_