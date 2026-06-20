# Curieri Romania

**Curieri Romania** este o integrare custom pentru **Home Assistant**, dezvoltata de **HAForge Labs**, pentru monitorizarea coletelor primite si trimise prin curierii din Romania.

Integrarea detecteaza coletele disponibile in conturile configurate, afiseaza statusurile intr-un panel dedicat si poate trimite notificari cand apare un colet nou sau cand un colet isi schimba statusul.

> Proiectul este in prima versiune publica stabila: **v1.0.0**.

---

## Functionalitati principale

- Monitorizare colete prin mai multi curieri din Romania.
- Panel dedicat in Home Assistant: **Curieri Romania**.
- Afisare colete active, livrate, disponibile la ridicare si cu probleme.
- Cautare, sortare si grupare colete.
- Detalii colet cu AWB, curier, expeditor, destinatar, locatie, ramburs, status si ultima actualizare.
- Istoric livrare, unde curierul returneaza aceste date.
- Statusuri originale pastrate si statusuri normalizate comune.
- Notificari pentru colet nou si schimbari relevante de status.
- Notificari mobile configurabile, cu fallback pe notificari persistente Home Assistant.
- Teste pentru notificari, utile cand nu exista colete active.
- Diagnostic notificari si diagnostic per curier.
- Sistem de licentiere global pentru integrare.
- Design responsive pentru desktop si mobil.
- Suport pentru tema light si dark.

---

## Curieri suportati in v1.0.0

| Curier | Status | Metoda recomandata de autentificare |
|---|---:|---|
| Sameday | Functional | Telefon si parola |
| FAN Courier | Functional | Username/email si parola |
| Cargus | Functional | Email, parola si telefon cont |
| GLS | Functional | Email si parola |

Unele metode avansate, cum ar fi helper/bookmarklet sau refresh token manual, pot ramane disponibile pentru diagnostic sau cazuri speciale.

---

## Statusuri normalizate

Integrarea pastreaza statusul original primit de la curier, dar il mapeaza si catre un status comun, pentru afisare unitara in Home Assistant.

Statusuri normalizate folosite:

- necunoscut
- inregistrat
- preluat
- in tranzit
- in depozit
- in livrare
- disponibil la locker
- disponibil la punct de ridicare
- livrat
- livrare esuata
- amanat
- returnat
- anulat
- problema

---

## Instalare manuala

1. Descarca arhiva release-ului.
2. Copiaza folderul:

```text
custom_components/curieri_romania
```

in Home Assistant, la:

```text
/config/custom_components/curieri_romania
```

3. Reporneste Home Assistant.
4. Mergi la:

```text
Settings > Devices & services > Add integration
```

5. Cauta **Curieri Romania**.
6. Adauga mai intai intrarea de administrare, apoi adauga curierii doriti.

Dupa actualizare, este recomandat restart Home Assistant si refresh fortat in browser pentru incarcarea corecta a panelului.

---

## Configurare

Integrarea foloseste o intrare globala de administrare si intrari separate pentru fiecare curier.

Ordinea recomandata:

1. Adauga integrarea **Curieri Romania** pentru administrare.
2. Configureaza licenta, daca ai una.
3. Adauga fiecare curier dorit ca intrare separata.
4. Verifica panelul **Curieri Romania** din sidebar.
5. Configureaza notificarile din tabul **Setari**.

---

## Panel dedicat

Panelul **Curieri Romania** include urmatoarele taburi:

- **Acasa** - vedere generala si ultimele colete actualizate.
- **Colete** - lista completa de colete, cu sortare, cautare si grupare.
- **Licenta** - status licenta, introducere cod, verificare si sustinere proiect.
- **Setari** - notificari, teste notificari, diagnostic si helper-e.
- **Contact** - linkuri utile, suport si informatii despre proiect.

Panelul este optimizat pentru desktop si mobil. Exista si un buton optional de iesire din panel, util in modul kiosk sau cand sidebar-ul Home Assistant este ascuns.

---

## Notificari

Integrarea poate trimite notificari cand:

- apare un colet nou;
- un colet isi schimba statusul;
- coletul intra in livrare;
- coletul devine disponibil la locker sau punct de ridicare;
- coletul este livrat;
- apare o problema de livrare;
- coletul este returnat.

Din tabul **Setari** poti configura serviciul de notificare mobil, de exemplu:

```text
notify.mobile_app_nume_telefon
```

Daca serviciul ales nu este disponibil sau nu este configurat, integrarea foloseste automat notificari persistente Home Assistant.

---

## Licentiere si sustinere

Curieri Romania include un sistem de licentiere global pentru integrare.

Licenta activa deblocheaza toti curierii configurati. Fara licenta activa, integrarea poate ramane limitata la primul curier configurat.

Licenta se poate obtine prin sustinerea proiectului pe Buy Me a Coffee:

```text
https://www.buymeacoffee.com/haforgelabs
```

La donatie, este important sa mentionezi:

```text
Curieri Romania
```

si adresa de email pe care doresti sa primesti licenta.

Daca ai deja licenta activa si integrarea iti este utila, poti sustine in continuare dezvoltarea, mentenanta si adaptarile necesare cand Home Assistant sau portalurile curierilor se schimba.

---

## Confidentialitate si date sensibile

Datele de curierat pot contine informatii sensibile, inclusiv:

- AWB-uri;
- nume expeditor/destinatar;
- adrese;
- telefoane;
- PIN-uri locker;
- sume ramburs;
- tokenuri si sesiuni de autentificare.

Pentru siguranta:

- nu publica loguri care contin tokenuri, cookie-uri sau date personale;
- nu trimite public capturi cu AWB complet, nume, adrese, telefoane sau PIN-uri;
- mascheaza datele personale cand ceri suport;
- nu distribui fisierul `.storage` din Home Assistant.

Integrarea evita logarea inutila a datelor sensibile, dar responsabilitatea pentru capturi, loguri si fisiere partajate ramane la utilizator.

---

## Diagnostic si depanare

Daca un curier nu afiseaza colete:

1. Verifica daca autentificarea este inca valida.
2. Verifica pagina dispozitivului din Home Assistant.
3. Verifica tabul **Setari** din panel.
4. Verifica diagnosticul per curier.
5. Verifica logurile Home Assistant, fara sa le publici nemascate.

Daca notificarile nu ajung pe mobil:

1. Verifica serviciul selectat in dropdown.
2. Ruleaza **Notificare simpla** din zona de test.
3. Verifica diagnosticul notificari.
4. Daca serviciul mobil nu functioneaza, integrarea ar trebui sa foloseasca fallback pe notificari persistente.

---

## Observatii importante

Aceasta integrare nu este dezvoltata, afiliata sau aprobata oficial de Sameday, FAN Courier, Cargus, GLS sau Home Assistant.

Portalurile si aplicatiile curierilor se pot modifica fara notificare. Daca un curier schimba API-ul, autentificarea sau structura datelor, poate fi necesara actualizarea integrarii.

---

## Roadmap

Directii posibile pentru versiuni viitoare:

- integrare prin HACS;
- suport pentru curieri suplimentari;
- filtrare mai avansata in panel;
- separare clara colete primite / trimise, unde datele permit;
- optiuni suplimentare pentru perioada de pastrare a istoricului;
- export diagnostic sigur;
- documentatie extinsa pentru fiecare curier.

---

## Suport

Website:

```text
https://haforgelabs.ro
```

Buy Me a Coffee:

```text
https://www.buymeacoffee.com/haforgelabs
```

Email suport:

```text
contact@haforgelabs.ro
```

Cand ceri suport, te rog sa maschezi datele personale si sa mentionezi:

- versiunea integrarii;
- versiunea Home Assistant;
- curierul afectat;
- mesajul de eroare relevant, fara tokenuri sau date personale.

---

## Licenta

Acest proiect este distribuit sub licenta **GNU General Public License v3.0**.

Vezi fisierul [LICENSE](LICENSE) pentru detalii.

Copyright (C) 2026 HAForge Labs.
