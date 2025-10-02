# Quick Start Guide

## Overview

This guide provides a streamlined path to get Tesira2MQTT running in under 5 minutes. For detailed information, see the [main README](../../README.md) and [full documentation](../index.md).

## Prerequisites

- **MQTT Broker**: Access to an MQTT broker
- **Biamp Tesira DSP**: A Tesira device with network connectivity
- **Docker**: For the easiest installation

## 5-Minute Setup

### Step 1: Create Configuration Directory

```bash
mkdir tesira2mqtt && cd tesira2mqtt
```

### Step 2: Create Configuration File

Create a file called `config.yaml` with the following contents:

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

### Step 3: Configure Your Settings

Edit the `config.yaml` file and replace the placeholder values with your actual:

- **MQTT Broker Details**: `server`, `user`, `password`
- **Tesira Device**: `host` (IP address or hostname)
- **Device Subscriptions**: `instance_tag` values from your Tesira configuration

### Step 4: Create Docker Compose File

Create a `docker-compose.yml` file with the following content:

```yaml
version: '3.8'

services:
  tesira2mqtt:
    image: rohankapoorcom/tesira2mqtt:latest
    container_name: tesira2mqtt
    restart: unless-stopped
    volumes:
      - ./config.yaml:/app/config.yaml:ro
    environment:
      - LOGLEVEL=info
```

### Step 5: Start the Service

```bash
docker-compose up -d
```

### Step 6: Verify It's Working

```bash
# Check logs
docker-compose logs tesira2mqtt

# Look for these success messages:
# INFO: Connecting to Tesira at your-tesira-device.com:23
# INFO: Successfully connected to Tesira device
# INFO: MQTT connection established
# INFO: Published availability status: online
```

## Home Assistant Integration

Your devices should automatically appear in Home Assistant within a few minutes. Look for:
- **Level Controls**: Slider controls for audio level adjustment
- **Mute Switches**: Toggle switches for mute functionality

If devices don't appear automatically:
1. Go to Configuration → Integrations → MQTT
2. Ensure MQTT integration is configured
3. Check Home Assistant logs for MQTT errors

## Quick Troubleshooting

### Application Won't Start
- Check configuration syntax: `python -c "import yaml; yaml.safe_load(open('config.yaml'))"`
- Verify MQTT broker credentials
- Ensure Tesira device is accessible

### No Devices in Home Assistant
- Check MQTT broker connectivity
- Verify discovery messages: `mosquitto_sub -h your-broker -t "homeassistant/#" -v`
- Restart Home Assistant MQTT integration

### Devices Don't Respond
- Verify instance tags match your Tesira configuration
- Check Tesira device logs
- Ensure proper network connectivity

## Next Steps

- **Multiple Devices**: Add more subscriptions to your config.yaml
- **Advanced Configuration**: See [Configuration Schema](../configuration/config-schema.md)
- **Full Documentation**: See [Documentation Index](../index.md)

## Need Help?

- **Issues**: [GitHub Issues](https://github.com/rohankapoorcom/tesiratomqtt/issues)
- **Discussions**: [GitHub Discussions](https://github.com/rohankapoorcom/tesiratomqtt/discussions)
- **Documentation**: [Full Documentation](../index.md)

---

**Last Updated**: September 2025
\*\*[^*]*Version\*\*: 1.1.7