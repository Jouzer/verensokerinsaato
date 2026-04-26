# Verensokerin Saadon Dashboard

Ensimmainen pala on tekstipohjainen simulaatiomalli. Dashboard rakennetaan
myohemmin taman rajapinnan paalle.

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

## Dashboard

Paikallinen Dash-dashboard:

```powershell
python app.py
```

Selainosoite:

```text
http://127.0.0.1:8050
```

Railway-kaynnistys on maaritelty tiedostossa `Procfile`:

```text
web: gunicorn app:server --bind 0.0.0.0:$PORT --workers 1
```

GUI:n kannalta oleellinen rajapinta on tiedostossa `src/simulation.py`:

- `SimulationInputs`
- `SimulationOutputs`
- `GlucoseControlSimulation.step(inputs)`

Taman ansiosta koko `simulation.py` voidaan vaihtaa myohemmin toiseen
toteutukseen, kun lopullinen Simulinkista portattu malli on tiedossa.

## Mallin nykyinen idea

Prosessilohkot on maaritelty `python-control`-transfer funktioina ja muunnettu
diskreeteiksi lohkoiksi liveajoa varten. Simulaatio etenee yhden aika-askeleen
kerrallaan, joten dashboardin ei tarvitse laskea koko vastetta uudestaan joka
paivityksella.

PID-saatimet ovat mallin sisalla, mutta ne on toteutettu erillisina
laskentalohkoina, jotta ulostulorajat ja kahden vastakkaisen saatajan
aktivointilogiikka pysyvat selkeina. Ne voidaan myohemmin vaihtaa tarkemmin
Simulink-rakennetta vastaaviksi lohkoiksi, jos lopullinen malli sita vaatii.
