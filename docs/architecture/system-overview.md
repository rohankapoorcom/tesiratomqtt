# System Architecture Overview

## Overview

Tesira2MQTT is a bidirectional MQTT bridge that enables control and monitoring of Biamp Tesira Digital Signal Processors (DSPs). The system architecture is designed for reliability, scalability, and ease of integration with Home Assistant and other MQTT-based systems.

## High-Level Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Home Assistant │    │   MQTT Broker   │    │  Tesira2MQTT   │
│                 │    │                 │    │                 │
│  ┌─────────────┐ │    │  ┌─────────────┐ │    │ ┌─────────────┐ │
│  │   Entities  │ │◄──►│  │   Topics    │ │◄──►│ │ MQTT Client │ │
│  │             │ │    │  │             │ │    │ │             │ │
│  └─────────────┘ │    │  └─────────────┘ │    │ └─────────────┘ │
│                 │    │                 │    │                 │
│  ┌─────────────┐ │    │  ┌─────────────┐ │    │ ┌─────────────┐ │
│  │  Discovery  │ │◄──►│  │ Discovery   │ │◄──►│ │ Discovery   │ │
│  │  Messages   │ │    │  │ Messages    │ │    │ │ Publisher   │ │
│  └─────────────┘ │    │  └─────────────┘ │    │ └─────────────┘ │
└─────────────────┘    └──────────────────┘    │                 │
                                                │ ┌─────────────┐ │
                                                │ │ Tesira      │ │
                                                │ │ Connection │ │
                                                │ │ Manager    │ │
                                                │ └─────────────┘ │
                                                │                 │
                                                │ ┌─────────────┐ │
                                                │ │ Telnet     │ │
                                                │ │ Clients    │ │
                                                │ └─────────────┘ │
                                                └─────────────────┘
                                                         │
                                                         ▼
                                                ┌─────────────────┐
                                                │  Biamp Tesira  │
                                                │      DSP       │
                                                │                 │
                                                │ ┌─────────────┐ │
                                                │ │   Device    │ │
                                                │ │ Instances   │ │
                                                │ └─────────────┘ │
                                                └─────────────────┘
```

## Component Architecture

### Core Components

#### 1. MQTT Connection Manager (`mqtt_connection.py`)

**Responsibilities:**
- Manages MQTT broker connections
- Publishes device states and attributes
- Publishes Home Assistant discovery messages
- Handles MQTT message queuing and retry logic

**Key Features:**
- Automatic reconnection on connection loss
- QoS 2 for reliable message delivery
- Retained messages for state persistence
- Topic validation and sanitization

#### 2. Tesira Connection Manager (`tesira.py`)

**Responsibilities:**
- Manages telnet connections to Tesira devices
- Handles device subscriptions and commands
- Implements automatic resubscription
- Manages connection pooling and semaphores

**Key Features:**
- Dual telnet connections (subscription + command)
- Automatic reconnection and resubscription
- Command queuing and timeout handling
- Serial number validation

#### 3. Configuration System (`models/`)

**Responsibilities:**
- Validates configuration data using Pydantic models
- Provides type safety and data validation
- Handles configuration serialization/deserialization
- Manages subscription deduplication

**Key Features:**
- YAML configuration validation
- Environment variable support
- Type-safe configuration access
- Automatic validation on startup

#### 4. Telnet Connection Handler (`telnet.py`)

**Responsibilities:**
- Low-level telnet communication with Tesira devices
- Command parsing and response handling
- Connection state management
- Error handling and recovery

**Key Features:**
- Async telnet communication
- Command/response parsing
- Connection health monitoring
- Automatic reconnection

## Data Flow Architecture

### 1. Initialization Flow

```
Application Start
       │
       ▼
Load Configuration ──► Validate Config ──► Parse Subscriptions
       │                     │                     │
       ▼                     ▼                     ▼
Create MQTT Client ──► Connect to Broker ──► Publish Availability
       │                     │                     │
       ▼                     ▼                     ▼
Create Tesira Client ──► Connect to Device ──► Get Serial Number
       │                     │                     │
       ▼                     ▼                     ▼
Subscribe to Devices ──► Publish Discovery ──► Start Monitoring
```

### 2. State Monitoring Flow

```
Tesira Device State Change
       │
       ▼
Telnet Response Received
       │
       ▼
Parse Response Data ──► Validate State Value ──► Update Internal State
       │                     │                     │
       ▼                     ▼                     ▼
Create MQTT Message ──► Publish State Topic ──► Publish Attributes Topic
       │                     │                     │
       ▼                     ▼                     ▼
Home Assistant Receives ──► Updates Entity State ──► User Sees Change
```

### 3. Command Execution Flow

```
User Command (Home Assistant)
       │
       ▼
MQTT Command Received
       │
       ▼
Parse Command Topic ──► Validate Command Data ──► Extract Device Info
       │                     │                     │
       ▼                     ▼                     ▼
Create Tesira Command ──► Send via Telnet ──► Wait for Response
       │                     │                     │
       ▼                     ▼                     ▼
Validate Response ──► Update Internal State ──► Publish State Update
       │                     │                     │
       ▼                     ▼                     ▼
Home Assistant Updates ──► User Sees Result ──► Command Complete
```

## MQTT Topic Architecture

### Topic Structure

```
{base_topic}/
├── availability                    # Service availability
├── {device_identifier}/
│   ├── state                       # Device state
│   ├── attributes                  # Device attributes
│   └── set                         # Command topic
└── discovery/
    ├── number/{unique_id}/config   # Level control discovery
    └── switch/{unique_id}/config   # Mute control discovery
```

### Topic Examples

```
tesira2mqtt/
├── availability
├── office_speakers_pc_level/
│   ├── state
│   ├── attributes
│   └── set
├── office_speakers_pc_mute/
│   ├── state
│   ├── attributes
│   └── set
└── homeassistant/
    ├── number/tesira2mqtt_office_speakers_pc_level/config
    └── switch/tesira2mqtt_office_speakers_pc_mute/config
```

## Error Handling Architecture

### Error Types

#### 1. Connection Errors
- **MQTT Connection Loss**: Automatic reconnection with exponential backoff
- **Tesira Connection Loss**: Automatic reconnection and resubscription
- **Network Timeouts**: Retry with increasing delays

#### 2. Command Errors
- **Invalid Commands**: Validation and error logging
- **Command Timeouts**: Retry mechanism with fallback
- **Device Errors**: Error propagation to MQTT topics

#### 3. Configuration Errors
- **Invalid Configuration**: Startup validation with clear error messages
- **Missing Fields**: Default value assignment where possible
- **Type Errors**: Automatic type conversion and validation

### Error Recovery

```
Error Detected
       │
       ▼
Log Error Details ──► Determine Error Type ──► Select Recovery Strategy
       │                     │                     │
       ▼                     ▼                     ▼
Connection Error ──► Reconnection Attempt ──► Resubscription
       │                     │                     │
       ▼                     ▼                     ▼
Command Error ──► Retry with Backoff ──► Fallback to Last Known State
       │                     │                     │
       ▼                     ▼                     ▼
Config Error ──► Validation Error ──► Application Shutdown
```

## Scalability Considerations

### Horizontal Scaling

- **Multiple Tesira Devices**: Each device requires separate telnet connections
- **MQTT Broker Clustering**: Support for clustered MQTT brokers
- **Load Distribution**: Multiple application instances can share MQTT topics

### Vertical Scaling

- **Connection Pooling**: Efficient telnet connection management
- **Async Operations**: Non-blocking I/O for high concurrency
- **Memory Management**: Efficient subscription and state management

### Performance Optimization

- **Connection Reuse**: Persistent telnet connections
- **Message Batching**: Batch MQTT messages where possible
- **Caching**: Cache device states to reduce Tesira queries
- **Compression**: MQTT message compression for large payloads

## Security Architecture

### Network Security

- **TLS/SSL**: Support for encrypted MQTT connections
- **Authentication**: MQTT username/password authentication
- **Network Isolation**: Tesira devices on isolated networks

### Application Security

- **Input Validation**: All inputs validated and sanitized
- **Command Validation**: Tesira commands validated before execution
- **Error Handling**: Secure error messages without sensitive data

### Data Security

- **Configuration Encryption**: Sensitive configuration data protection
- **Log Sanitization**: Sensitive data removed from logs
- **Access Control**: MQTT topic access control

## Monitoring and Observability

### Health Checks

- **MQTT Connection**: Periodic connection health checks
- **Tesira Connection**: Telnet connection monitoring
- **Service Availability**: Overall service health status

### Metrics

- **Connection Metrics**: Connection success/failure rates
- **Command Metrics**: Command execution times and success rates
- **Message Metrics**: MQTT message publish rates and errors

### Logging

- **Structured Logging**: JSON-formatted logs for easy parsing
- **Log Levels**: Configurable log levels for different environments
- **Log Rotation**: Automatic log rotation and cleanup

## Deployment Architecture

### Container Deployment

```
Docker Host
├── Tesira2MQTT Container
│   ├── Application Code
│   ├── Configuration Volume
│   ├── Log Volume
│   └── Network Access
├── MQTT Broker Container
└── Monitoring Container
```

### Network Topology

```
Internet
    │
    ▼
Firewall/Router
    │
    ▼
Local Network
├── MQTT Broker
├── Tesira2MQTT
├── Home Assistant
└── Tesira Devices
```

---

**Last Updated**: September 2025
**Architecture Version**: 1.1.17
