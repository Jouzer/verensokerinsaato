# Verensokerin Saadon Dashboard
Vibekoodattu GPT5.5 avulla, kouluprojekti
Huomio! Tätä koodia ei miltään osalta ole tarkoitettu lääkinnällisiin tarkoituksiin, kyseessä on teoreettinen automaatioalan harjoitustyö.

## Asennus

```powershell
python -m pip install -r requirements.txt
```

## Tekstisimulaation ajo

```powershell
python run_simulation.py
```

Live-tilan tekstidemo:

```powershell
python run_live.py
```

Live-tilassa `h` lisaa hiilihydraattitapahtuman, `l` lisaa liikuntatapahtuman
ja `q` lopettaa ajon.

Liikunnan automaattinen testiajo:

```powershell
python run_live.py --exercise-at 0 --exercise-minutes 30 --exercise-intensity 0.7
```

Simulink-kaaviosta portatun rinnakkaismallin tekstiajo:

```powershell
python run_simulation_diagram.py
```

Uusi malli on tiedostossa `src/simulation_diagram.py`. Se kayttaa samaa
`SimulationInputs` -> `SimulationOutputs` -rajapintaa kuin alkuperainen
`src/simulation.py`, mutta sisainen rakenne seuraa Simulink-lohkokaaviota
tarkemmin.

## Dashboard

Paikallinen Dash-dashboard:

```powershell
python app.py
```

Railway-kaynnistys on määritelty tiedostossa `Procfile`:

```text
web: gunicorn app:server --bind 0.0.0.0:$PORT --workers 1
```

GUI:n kannalta oleellinen rajapinta on tiedostossa `src/simulation.py`:

- `SimulationInputs`
- `SimulationOutputs`
- `GlucoseControlSimulation.step(inputs)`

Taman ansiosta koko `simulation.py` voidaan vaihtaa myähemmin toiseen
toteutukseen, kun lopullinen Simulinkista portattu malli on tiedossa.

## Mallin nykyinen idea

Prosessilohkot on maaritelty `python-control`-transfer funktioina ja muunnettu
diskreeteiksi lohkoiksi liveajoa varten. Simulaatio etenee yhden aika-askeleen
kerrallaan, jotta dashboardin ei tarvitse laskea koko vastetta uudestaan joka
päivityksella.

PID-saatimet ovat mallin sisalla, mutta ne on toteutettu erillisina
laskentalohkoina, jotta ulostulorajat ja kahden vastakkaisen saatajan
aktivointilogiikka pysyvät selkeinä. Ne voidaan myohemmin vaihtaa tarkemmin
Simulink-rakennetta vastaaviksi lohkoiksi, jos lopullinen malli sita vaatii.
