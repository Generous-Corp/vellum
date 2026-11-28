---
# Synthetic DESIGN.md fixture: exercises edges the upstream
# paws-and-paths file does not (block scalars, quoted strings with
# colons, fontFeature/fontVariation, unresolved refs, unknown sections).
version: alpha
name: Sample Test Fixture
description: |
  Multi-line description using YAML block scalar.
  Tests that yaml-cpp handles the `|` form correctly without
  collapsing the content into a single line.

colors:
  primary: "#1A1C1E"
  secondary: "#6C7278"
  accent: "{colors.primary}"      # token reference, resolved at parse-time
  broken: "{colors.does-not-exist}"  # exercises the broken-ref diagnostic

typography:
  body-md:
    fontFamily: "Inter, sans-serif"   # quoted string containing a comma
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.6
    fontFeature: "ss01, cv11"
    fontVariation: "wght 400, slnt 0"

rounded:
  sm: 4px
  md: 8px

spacing:
  base: 16px
  weird: "5"                          # non-dimension value preserved as string

components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "#ffffff"
    typography: "{typography.body-md}"
    rounded: "{rounded.sm}"
    padding: 12px
---

# Sample Test Fixture

## Overview

A minimal DESIGN.md authored for the test suite to exercise spec
edges that the upstream paws-and-paths example does not.

## Colors

Primary is `#1A1C1E`. The `accent` token references it so the parser
resolves the reference into the same hex value.

## Typography

Body uses Inter; `fontFeature` and `fontVariation` carry OpenType
feature and variation axis settings through DTCG output verbatim.

## Iconography

This is an UNKNOWN section heading (not in the canonical spec order).
The parser must preserve it without erroring.

## Components

Components reference both color and composite typography tokens to
verify the in-components-only group-ref permission.
