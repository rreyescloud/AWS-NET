# AWS Network Firewall - Overview

## What is AWS Network Firewall?

AWS Network Firewall is a managed, stateful network firewall and intrusion detection/prevention service for VPCs. It operates at **Layers 4-7** of the OSI model, providing:

- Stateless packet filtering (Layer 4)
- Stateful traffic inspection (Layer 4-7)
- Intrusion Detection/Prevention (IDS/IPS) via Suricata engine
- Domain-based filtering (HTTP/HTTPS)
- TLS inspection capabilities

## Where It Sits in the VPC

Network Firewall is deployed as **firewall endpoints** within dedicated subnets in your VPC. Key points:

- Installed on a per-Availability Zone basis
- Cannot protect its own subnet
- Can protect all other subnets in the VPC
- Traffic is routed to the firewall endpoint via VPC route tables

## Stateless vs Stateful Processing

### Stateless Engine
- Evaluates each packet in **isolation** (no connection tracking)
- Processes based on standard network attributes (IP, port, protocol)
- Applied to all network packets immediately

### Stateful Engine
- Evaluates packets in the context of a **traffic flow**
- Maintains connection state
- Supports Suricata-compatible rules
- Applied only to new traffic flows (does not affect existing connections)
- Supports domain list filtering, standard stateful rules, and Suricata rule strings

## How Network Firewall Processes Traffic (TCP + TLS)

The following diagram shows how Network Firewall handles a HTTPS connection and inspects the SNI (Server Name Indication) in the TLS ClientHello to apply domain-based filtering:

```
Cliente                        Firewall                      Servidor
  |                               |                              |
  |---- TCP SYN --------------->  |                              |
  |    (solo IPs, no dominio)     | "No se el dominio aun,      |
  |                               |  dejo pasar el handshake"    |
  |                               |------- TCP SYN ------------>  |
  |<------------------------------|<---- TCP SYN-ACK ------------|
  |---- TCP ACK --------------->  |------- TCP ACK ------------>  |
  |                               |                              |
  |                               |                              |
  |-- TLS ClientHello --------->  |                              |
  |   SNI: "www.google.com"      | "Ahora si se el dominio!     |
  |                               |  Verifico mis reglas..."     |
  |                               |  * dominio permitido         |
  |                               |--- TLS ClientHello -------->  |
  |                               |                              |
  |<==============================|<=== todo cifrado de aqui ====|
  |=== trafico cifrado =========>|=== en adelante ==============>|
```

**Key takeaway:** During the TCP 3-way handshake, NF only sees IP addresses. Domain-based filtering can only occur once the TLS ClientHello reveals the SNI field. This is why the handshake is allowed through before domain rules take effect.

## Core Architecture Components

### 1. Firewall
- Connects the firewall policy to the VPC it protects
- Deployed per-AZ as firewall endpoints
- Requires one firewall policy
- Configuration includes traffic logging and stateful filtering settings

### 2. Firewall Policy
- Reusable set of stateless and stateful rule groups
- Defines network traffic filtering behavior
- Can be shared across multiple firewalls
- Settings include:
  - Stream exception policy (handles midstream connection breaks)
  - Stateless rule groups (0 or more)
  - Stateful rule groups (0 or more)
  - Default actions for both stateless and stateful rules
  - TCP idle timeouts: 60-6000 seconds (default: 350)
  - TLS inspection configuration (optional)
  - Policy variables (HOME_NET, EXTERNAL_NET)

### 3. Rule Groups
- Reusable collections of criteria for inspecting traffic
- Types: Stateless and Stateful
- Stateful categories:
  - **Suricata compatible rule strings** - full Suricata syntax
  - **Domain list** - list of domain names with protocol type
  - **Standard stateful rules** - standard network connection attributes
- Maximum capacity: 30,000 for both stateless and stateful
- Capacity is fixed at creation time and cannot be changed

## Critical Relationships

1. Firewall -> Firewall Policy (1:1)
2. Firewall Policy -> Rule Groups (1:many)
3. Firewall -> VPC (protection relationship)
4. Firewall Endpoints -> Availability Zones (deployment)

## Underlying Infrastructure

- Network Firewall uses **Gateway Load Balancer** (GWLB) internally
- GWLB uses GENEVE protocol on port 6081
- Target selection: 5-tuple (default), 3-tuple, or 2-tuple
- Session state sharing is enabled across firewall instances in the same AZ (eliminates drops during scaling/maintenance)
- NFW is a managed service - scaling events occur transparently

## References

- [Firewall Components](https://docs.aws.amazon.com/network-firewall/latest/developerguide/firewall-components.html)
- [Firewall Settings](https://docs.aws.amazon.com/network-firewall/latest/developerguide/firewall-settings.html)
- [VPC Subnet Configuration](https://docs.aws.amazon.com/network-firewall/latest/developerguide/vpc-config-subnets.html)
- [VPC Route Table Configuration](https://docs.aws.amazon.com/network-firewall/latest/developerguide/vpc-config-route-tables.html)
- [Getting Started](https://docs.aws.amazon.com/network-firewall/latest/developerguide/getting-started.html)
- [Target Groups for GWLB](https://docs.aws.amazon.com/elasticloadbalancing/latest/gateway/target-groups.html#flow-stickiness)
- [Introducing GWLB Target Failover for Existing Flows](https://aws.amazon.com/blogs/networking-and-content-delivery/introducing-aws-gateway-load-balancer-target-failover-for-existing-flows/)
- [Best Practices for Deploying Gateway Load Balancer](https://aws.amazon.com/blogs/networking-and-content-delivery/best-practices-for-deploying-gateway-load-balancer/)
