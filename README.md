# Tesira2MQTT - MQTT Bridge for Biamp Tesira DSPs

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?style=flat&logo=docker&logoColor=white)](https://www.docker.com/)

Tesira2MQTT is a powerful MQTT bridge application that enables seamless control of Biamp Tesira Digital Signal Processors (DSPs) through MQTT protocol. It provides bidirectional communication between MQTT brokers and Biamp Tesira audio processing units, allowing for level control and muting functionality. The application automatically publishes Home Assistant discovery messages for instant entity configuration and device management.

## 🎯 Key Features

- **Bidirectional MQTT Communication**: Control and monitor Biamp Tesira DSPs via MQTT
- **Home Assistant Integration**: Automatic discovery and entity configuration
- **Level Control**: Adjust audio levels for various input/output channels
- **Mute Control**: Enable/disable mute functionality for audio channels
- **Real-time Monitoring**: Subscribe to device state changes and publish updates
- **Resilient**: Reconnects to the Tesira and the MQTT broker independently; a broker redeploy does not restart the app or disturb the Tesira
- **Health checks**: HTTP probes for Docker and Kubernetes (`/livez`, `/readyz`, `/health`)
- **Docker Support**: Easy deployment with containerized application
- **Flexible Configuration**: YAML-based configuration with validation
- **Asynchronous Operations**: High-performance async/await implementation

## 🚀 Quick Start

**Want to get running in 5 minutes?** See our [Quick Start Guide](docs/user-guides/quick-start.md) for a streamlined setup process.

### Prerequisites

- Python 3.13+ or Docker
- MQTT broker (e.g., Mosquitto, Home Assistant MQTT broker)
- Biamp Tesira DSP with network connectivity
- Network access between the application and both MQTT broker and Tesira device

### Docker Compose Installation (Recommended)

Create a `docker-compose.yml` file with the following content:

```yaml
version: '3.8'

services:
  tesira2mqtt:
    image: rohankapoorcom/tesira2mqtt:latest
    container_name: tesira2mqtt
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - ./config.yaml:/app/config.yaml:ro
    environment:
      - LOGLEVEL=info
```

Create your configuration file (see Configuration section below), then start the service:

```bash
docker-compose up -d
```

### Docker Run Installation (Alternative)

```bash
# Create your configuration file (see Configuration section below)
# Edit config.yaml with your MQTT broker and Tesira device details

# Run with Docker
docker run -d \
  --name tesira2mqtt \
  -p 8080:8080 \
  -v $(pwd)/config.yaml:/app/config.yaml \
  --restart unless-stopped \
  rohankapoorcom/tesira2mqtt:latest
```

### Local Installation

```bash
# Clone the repository
git clone https://github.com/rohankapoorcom/tesiratomqtt.git
cd tesiratomqtt

# Install dependencies
pip install -r requirements.txt

# Copy and customize configuration
cp config.yaml.example config.yaml
# Edit config.yaml with your MQTT broker and Tesira device details

# Run the application
python -m src
```

## ⚙️ Configuration

### Basic Configuration

Create a `config.yaml` file with your MQTT broker and Tesira device details:

```yaml
mqtt:
  base_topic: tesira2mqtt
  server: your-mqtt-broker.com
  port: 1883
  user: your-mqtt-username
  password: your-mqtt-password
  keepalive: 60

tesira:
  host: your-tesira-device.com
  port: 23
  resubscription_time: 300

subscriptions:
  - instance_tag: OfficeSpeakersPCLevel
    attribute: level
    index: 1
    name: Level
    device_name: Office Speakers PC
  - instance_tag: OfficeSpeakersPCLevel
    attribute: mute
    index: 1
    name: Mute
    device_name: Office Speakers PC
```

### Configuration Details

For complete configuration documentation including:
- Detailed field descriptions and validation rules
- Advanced configuration examples
- Troubleshooting and best practices
- Environment variable options

**See the [Configuration Schema Documentation](docs/configuration/config-schema.md)**

## 🏠 Home Assistant Integration

Tesira2MQTT automatically publishes Home Assistant discovery messages, making integration seamless:

1. **Automatic Discovery**: Devices appear in Home Assistant automatically
2. **Entity Configuration**: Each subscription becomes a controllable entity
3. **Device Grouping**: Related attributes are grouped under device names
4. **State Monitoring**: Real-time state updates and control

### Home Assistant Entities

- **Level Controls**: Slider controls for audio level adjustment
- **Mute Switches**: Toggle switches for mute functionality
- **Availability Sensors**: Monitor device connectivity status

## 📊 Monitoring and Logging

### Log Levels

- `DEBUG`: Detailed operation information
- `INFO`: General operation status
- `WARNING`: Non-critical issues
- `ERROR`: Error conditions
- `CRITICAL`: Critical failures

### Log Configuration

Set log level via command line or environment variable:

```bash
# Command line
python -m src --loglevel debug

# Environment variable
export LOGLEVEL=debug
python -m src
```

## Health checks

Port `8080` by default (`health` in `config.yaml`):

- `GET /livez` — process is up (Kubernetes liveness)
- `GET /readyz` / `GET /health` — MQTT and Tesira telnet are both connected (Kubernetes readiness)

The image `HEALTHCHECK` probes `$HEALTHCHECK_URL` (`http://127.0.0.1:8080/health` by default). If you disable the listener or change `health.port`, override `HEALTHCHECK_URL` or the image health check.

## 🔧 Troubleshooting

### Common Issues

#### Connection Issues

**Problem**: Cannot connect to MQTT broker
```
Solution:
- Verify MQTT broker credentials and network connectivity
- Check firewall settings
- Ensure MQTT broker is running and accessible
- Broker outages are not fatal: the app logs "MQTT connection lost" and
  reconnects with backoff, republishing everything once the broker is back
```

**Problem**: Cannot connect to Tesira device
```
Solution:
- Verify Tesira device IP address and port (default: 23)
- Check network connectivity between application and Tesira
- Ensure Tesira device has telnet enabled
- Verify Tesira device is not in use by other applications
```

#### Configuration Issues

**Problem**: Invalid configuration error
```
Solution:
- Validate YAML syntax
- Check required fields are present
- Verify data types match expected values
- Use configuration validation tools
```

#### Performance Issues

**Problem**: Slow startup or slow response
```
Solution:
- Startup takes ~6 seconds: the Tesira sets up telnet sessions one at a
  time at ~3 seconds each
- resubscription_time does not affect update latency; changes are pushed
  by the Tesira immediately
- Check network latency to both MQTT broker and Tesira
- Monitor log levels (avoid DEBUG in production)
```

**Problem**: `Unexpected serial number ... from Tesira` at startup
```
Solution:
- The reply to DEVICE get serialNumber was not a plain serial. Check it
  in a telnet session; it is used in MQTT topics and may only contain
  letters, digits, '_' and '-'
```

### Debug Mode

Enable debug logging for detailed troubleshooting:

```bash
python -m src --loglevel debug
```

This will show:
- MQTT connection status
- Tesira telnet communication
- Message publishing details
- Subscription management
- Error details and stack traces

## 🛠️ Development

### Development Environment Setup

#### Option 1: VSCode Dev Containers (Recommended)

The easiest way to get started with development is using VSCode Dev Containers:

1. **Install Prerequisites**:
   - [VSCode](https://code.visualstudio.com/)
   - [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)

2. **Open in Dev Container**:
   ```bash
   git clone https://github.com/rohankapoorcom/tesiratomqtt.git
   cd tesiratomqtt
   code .
   ```
   - VSCode will prompt to "Reopen in Container"
   - Click "Reopen in Container" to start the development environment

3. **Development Commands**:
   ```bash
   # Run linting
   scripts/lint

   # Run tests
   scripts/test

   # Run the application
   python -m src
   ```

#### Option 2: Local Python Setup

```bash
# Clone repository
git clone https://github.com/rohankapoorcom/tesiratomqtt.git
cd tesiratomqtt

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install runtime and development dependencies
pip install -r requirements-dev.txt

# Run linting
scripts/lint

# Run tests
scripts/test
```

### Tests

The tests run offline against a fake Tesira (`tests/fake_tesira.py`) and an in-memory broker (`tests/fake_mqtt.py`). Run them with `scripts/test`; pytest arguments pass through (`scripts/test -k reconnect -v`).

### Code Structure

```
src/
├── __init__.py          # Main application entry point
├── _version.py          # Version information
├── errors.py            # Custom exception classes
├── health.py            # HTTP liveness/readiness probes
├── models/              # Pydantic data models
│   └── __init__.py      # Configuration models
├── mqtt_connection.py   # MQTT client management
├── tesira.py            # Tesira device communication
├── telnet.py            # Telnet connection handling
└── utils/               # Utility functions
    └── arguments.py      # Command line argument handling
tests/
├── conftest.py          # Fixtures
├── fake_mqtt.py         # In-memory stand-in for aiomqtt.Client
├── fake_tesira.py       # Fake Tesira TTP server
├── test_bridge.py       # Both supervisors together
├── test_health.py
├── test_mqtt.py
├── test_telnet.py
└── test_tesira.py
```

### Tesira Text Protocol Reference

This application communicates with Biamp Tesira DSPs using the Tesira Text Protocol (TTP). For detailed information about TTP commands, responses, and subscription features, refer to the official Biamp documentation:

**[Tesira Text Protocol Documentation](https://support.biamp.com/Tesira/Control/Tesira_Text_Protocol)**

Key TTP concepts used in this application:
- **Subscriptions**: Automatic responses when DSP block states change
- **Commands**: `subscribe`, `unsubscribe`, `get`, `set` operations
- **Instance Tags**: Case-sensitive identifiers for DSP blocks
- **Attributes**: Specific elements within DSP blocks (e.g., `level`, `mute`)
- **Custom Labels**: Unique identifiers for subscription responses

## 🤝 Contributing

We welcome contributions! Please see our [Contributing Guidelines](CONTRIBUTING.md) for details.

### Development Workflow

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Update documentation if needed
5. Run tests and linting
6. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- Based on [integration_blueprint](https://github.com/ludeeus/integration_blueprint) by Joakim Sørensen (@ludeeus)
- Built for the Biamp Tesira DSP ecosystem
- Designed for Home Assistant integration

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/rohankapoorcom/tesiratomqtt/issues)
- **Discussions**: [GitHub Discussions](https://github.com/rohankapoorcom/tesiratomqtt/discussions)
- **Documentation**: [Full Documentation](docs/index.md)

---

**Last Updated**: September 2025
\*\*[^*]*Version\*\*: 1.1.6
