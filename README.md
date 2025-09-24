<p align="center">
  <img src="icon.png" alt="Solar Eclipse Prediction" width="140" />
</p>

<h1 align="center">Solar Eclipse Prediction</h1>

Integrazione personalizzata per Home Assistant (compatibile HACS) che mostra le prossime eclissi solari:
- Con Skyfield ON: calcolo locale (copertura, massimo locale, contatti stimati) alle coordinate impostate; mostra le prossime N eclissi realmente visibili.
- Con Skyfield OFF: mostra le prossime N eclissi per “Geographic Region of Eclipse Visibility” (es. Europe, North America).

Dati: cataloghi per decennio NASA/GSFC + calcoli locali tramite Skyfield.

## Requisiti
- Home Assistant 2023.8+ (o superiore)
- (Opzionale, per calcolo locale Skyfield)
  - skyfield>=1.49
  - jplephem>=2.22
  - numpy>=1.26
(Questi pacchetti sono dichiarati nel manifest e gestiti automaticamente da HA.)

## Installazione tramite HACS
1. Apri HACS → Integrations.
2. Clicca su “Custom repositories” (in alto a destra).
3. Inserisci il repo `https://github.com/<tuo-utente>/solar_eclipse` e scegli “Integration”.
4. Aggiungi il repository e installa “Solar Eclipse Prediction”.
5. Riavvia Home Assistant.
6. Vai in Impostazioni → Dispositivi e servizi → Aggiungi integrazione → cerca “Solar Eclipse Prediction”.

## Configurazione
- Use Skyfield
  - ON: verranno richieste latitudine/longitudine. L’integrazione calcola copertura locale, massimo locale e contatti (stime).
  - OFF: verrà richiesta la “Geographic Region of Eclipse Visibility” (Europa, Nord America, …), senza calcoli locali.
- Number of eclipses to show: quante eclissi esporre (1–10, default 3).
- Daily update hour: ora locale in formato HH:MM (i minuti sono ignorati, usare :00).
- Puoi cambiare in qualsiasi momento (Opzioni) senza rimuovere l’integrazione.

## Entità create
- `sensor.eclipse_1_date` … `sensor.eclipse_N_date` (classe: DATE, mostra solo la data)
  - Attributi principali (se disponibili):
    - region
    - type
    - local_max_coverage_percent (es. “45.1%”) [Skyfield ON]
    - start_time, maximum_time, end_time (HH:MM in fuso locale)
    - start, end (UTC dal dataset, se presenti)
    - source, attribution
- `sensor.days_until_next_eclipse` (icone: mdi:calendar-end)
- `binary_sensor.eclipse_this_week` (icone: mdi:telescope)

## Funzionamento
- Aggiornamento:
  - Subito al caricamento, poi ogni giorno all’ora locale configurata.
  - Cache in memoria (24h) per ridurre richieste.
- Skyfield ON:
  - Scansione della lista futura NASA e ricerca delle prossime N eclissi con copertura > 0% alle tue coordinate.
  - Calcoli con concorrenza limitata (semaforo) e “throttling” giornaliero.
- Skyfield OFF:
  - Filtra per regione (con ausilio JSEX). In caso di rete non disponibile, usa un fallback minimo.

## Icone, branding e traduzioni
- Icone MDI nelle entità.
- `icon.png` incluso nel repository per la pagina GitHub/HACS (non usabile come icona integrazione in HA: per quello serve PR a `home-assistant/brands` con il dominio `solar_eclipse`).
- Traduzioni incluse: en, it, es, fr, de.

## Risoluzione problemi
- Vedi “fallback” nei log:
  - La pagina NASA può essere momentaneamente non raggiungibile/parsabile. Verifica connettività verso `https://eclipse.gsfc.nasa.gov/SEdecade/...`.
  - Attendi il refresh giornaliero o ricarica l’integrazione.
- Sensori “unknown”:
  - Con Skyfield ON: se meno di N eclissi sono visibili a breve distanza temporale, alcuni slot restano vuoti finché non vengono trovati eventi più lontani.
  - Con Skyfield OFF: verifica la regione selezionata e la rete.

## Sicurezza e privacy
- Le richieste vanno a domini NASA/GSFC e JPL (HTTPS). Nessun invio di lat/long all’esterno: le coordinate sono usate solo per i calcoli in locale.
- L’ephemeris Skyfield (`de421.bsp`) è scaricato in `.storage/solar_eclipse_skyfield` e rimosso quando l’integrazione viene disinstallata.

## Crediti
- Dati: Eclipse predictions by NASA/GSFC
- Libreria astronomica: [Skyfield](https://rhodesmill.org/skyfield/)
- Autore: @<tuo-utente>

## Licenza
Apache License 2.0 — vedi `LICENSE.txt`.
