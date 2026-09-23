# Auto Rate Explorer

**County-level personal auto rating for all 50 states + DC, explained in plain English by a local LLM.**

Enter a vehicle, a city, and a driver. The app rates that same risk in **every county of the state**,
draws the result as a choropleth, breaks the premium into its rating factors, and has a model running
on your own machine explain *why* the rate is what it is.



> **3,144 counties · 49,951 places · 51 jurisdictions · 28 unit tests · no API keys · no cloud LLM**

---

## Quickstart

You need **Python 3.11+**. [Ollama](https://ollama.com/download) is optional but recommended —
it's what powers the explanations.

**macOS / Linux**

```bash
git clone https://github.com/RichMakhoul/AutoInsuranceLocalAICalc.git
cd AutoInsuranceLocalAICalc
ollama pull llama3.2:3b          # one time, ~2 GB (skip if you don't want the LLM)
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./run.sh                         # → http://localhost:5000
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/RichMakhoul/AutoInsuranceLocalAICalc.git
cd AutoInsuranceLocalAICalc
ollama pull llama3.2:3b
python -m venv venv
venv\Scripts\pip install -r requirements.txt
.\run.bat                        # → http://localhost:5000
```

Then open **http://localhost:5000**, type something like `2020 lamborghini huracan`, pick a city,
and hit **Rate across the state**. Click any county on the map, then **Explain this rate**.

All reference data is prebuilt and committed, so there is nothing to download from the Census or NAIC.
For a step-by-step walkthrough on a fresh machine (and troubleshooting), see **[SETUP.md](SETUP.md)**.

---
