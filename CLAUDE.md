# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WAIL (Website Agent Integration Layer) is a service that allows agencies and website owners to expose their sites to AI agents (ChatGPT, Claude, Gemini, etc.) via those agents' native protocols. Target markets are booking businesses (barbers, nail salons), ecommerce sites, and agency-managed showcase sites.

## Architecture

### Core Concept

WAIL is a **registry-based integration platform** — agents don't discover sites directly, WAIL is the centralized directory. A single MCP server / ChatGPT Action / Gemini Extension registered with WAIL gives agents access to all WAIL-registered sites.

```
[AI Agent] → [WAIL Server] → [Connector Layer] → [Client's data sources]
                  ↓
         [Protocol Adapters]
         MCP / OpenAPI / Gemini
```

### Stack

- **Backend:** Python — core system, connector layer, protocol adapters
- **Frontend:** Next.js — configuration dashboard for site owners/agencies

### Key Layers

**Connector Layer** (the core IP)
- Declarative per-site config mapping data sources to a normalized internal schema
- Source types: `api` (REST endpoints with auth) and `scrape` (CSS selector-based, read-only public data only)
- Start with one connector (API type) for v1 testing before building the framework broadly

**Protocol Adapter Layer**
- Internal schema is protocol-agnostic
- Adapters translate to: MCP (Claude), OpenAPI Actions (ChatGPT), Gemini Extensions
- Request routing based on User-Agent / Accept headers / explicit protocol param

**Registry**
- Site owners register their site and configure connectors via the Next.js dashboard
- WAIL holds the directory of all registered sites and their capabilities

### Auth / Permissions

Two-tier model:
- **Observe** (read data like availability, products, hours) — public, no auth required
- **Interact** (book, purchase, mutate) — requires an API key set by the business owner, optional per their config

### Discovery (intentional non-feature for v1)

WAIL does NOT rely on HTML tags, `llms.txt`, or `/.well-known/` auto-discovery — none of the major agents support auto-discovery from browsing today. WAIL itself is the discovery layer. This may be revisited as standards mature.
