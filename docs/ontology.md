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

Label

Media Channel

---

# Entity Classification

Entity **type is inferred, never asserted by a source adapter**. A platform handle
(e.g. a YouTube channel id) identifies a thing but says nothing about its kind — the same
channel shape can be an artist, a label, a promoter, a venue, or a media network.

Adapters therefore emit a **type-neutral source handle** (`youtube:channel:{id}`) plus
signals, and a classification step decides the entity type **before canonical resolution**.
Classification is deterministic-first (MusicBrainz cross-reference, platform topic metadata,
name/structure heuristics), with an AI fallback whose output is an observation carrying
confidence and provenance — never truth. Low-confidence typings route to review.

Example: the T-Series YouTube channel classifies as a **Label** (it aggregates film music
from many artists), not an Artist — so its reach signals attach to `label:t-series`, and are
never mistaken for a single artist's demand.

---

# Relationships

Artist

→ signed_to

Label

Label

→ releases

Creative

---

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
