# n-quark
## Ontology

---

# Core Entity Types

Artist

Organizer

Community

Event

Venue

City

Genre

Sponsor

Creative

Audience

Platform

Signal

Observation

Feature

Prediction

---

# Relationships

Organizer

→ runs

Community

Community

→ hosts

Event

Event

→ occurs_at

Venue

Event

→ features

Artist

Venue

→ located_in

City

Artist

→ belongs_to

Genre

Event

→ sponsored_by

Sponsor

Creative

→ promotes

Event

Audience

→ attends

Event

Artist

→ performs_for

Community

Observation

→ references

Entity

Prediction

→ generated_from

Features

---

# Observation Schema

Observation

- id
- entity
- attribute
- value
- source
- timestamp
- confidence
- evidence
- metadata

Observations are immutable.

---

# Feature Schema

Feature

- id
- entity
- name
- value
- version
- calculated_at
- confidence

Examples

historical_event_count

sellout_rate

average_ticket_price

regional_strength

community_overlap

venue_diversity

---

# Prediction Schema

Prediction

- id
- engine
- entity
- output
- confidence
- model_version
- feature_version
- generated_at

---

# Intelligence Outputs

Demand Score

Momentum

Venue Fit

Community Fit

Audience Match

Revenue Forecast

Attendance Forecast

Market Opportunity

Recommendation

Risk Score

---

# External Signals

Spotify

Instagram

YouTube

Google Trends

Weather

Ticketing

News

Government Calendars

Each signal is normalized into observations.

---

# Guiding Rules

Entities are canonical.

Observations are immutable.

Relationships are directional.

Everything is timestamped.

Everything has provenance.

Everything has confidence.

Nothing is deleted.
