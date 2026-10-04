# 🌾 Ming: Offline Market Intelligence & Negotiation Support for Smallholders

> **Ming** (明 — meaning clarity and light) is a channel-agnostic, offline-first market intelligence and negotiation support engine designed for smallholder farmers operating under severe connectivity and hardware constraints. Built for the **World Bank "Small AI for Development" Hackathon**.

---

## 🚀 The Core Problem
Smallholder farmers in remote regions frequently negotiate blind. Lacking access to transparent, up-to-date market reference prices due to a lack of internet or data plans, farmers fall victim to local middlemen who exploit this information asymmetry to impose unfair prices. Traditional agritech solutions fail because they demand expensive smartphones, 5G coverage, or massive data plans that do not match rural realities.

---

## 💡 The Ming Solution
Ming bridges the information gap by turning macro open data into actionable local bargaining power through an inclusive, dual-channel architecture:

1. **Feature-Phone Channel (USSD / SMS):** Farmers without internet or smartphones can query regional price benchmarks instantly using basic shortcodes (`*123#`) or plain text commands over standard cellular networks.
2. **Offline-First Smartphone PWA:** Uses local caching (`SQLite` store-and-forward) to store the latest market reference datasets, allowing offline consultations in the field that sync automatically when connectivity returns.

---

## 🛡️ Responsible AI & Ethical Guardrails
Ming rejects inflated AI hype in favor of engineering realism and farmer protection:
* **Explicit Abstention (Fail-Safe):** If reference data is outdated (>120 days) or too sparse for a specific market, Ming explicitly abstains (*"Insufficient regional data to evaluate"*), preventing false positives and misleading advice.
* **Radical Privacy (Zero PII):** No names, national IDs, or permanent user profiles are collected. Interactions are anchored to an anonymous, locally generated parcel hash.
* **Consent-Driven Escalation:** Raw phone numbers are never stored unless the farmer explicitly requests human technical assistance and consents to a callback.

---

## 📂 Repository Structure

```text
├── app.py              # Streamlit Web App (Offline-first smartphone PWA & UI demo)
├── core.py             # Agnostic core logic (Symptom tree, USSD/SMS engine, price checks)
├── simulator.py        # Feature-phone handset simulator (USSD/SMS interactive view)
├── server.py           # Production webhook gateway (Flask integration for USSD/SMS aggregators)
├── requirements.txt    # Python dependencies
└── data/               # Regional reference price datasets (WFP / HDX format)
