# Mot Lidingö

Träningsportal för simning och löpning fram till Kungsholmen Runt, Vansbrosimningen och Lidingöloppet 2027.
Visas på GitHub Pages från mappen `docs/`. Loggade pass sparas bara i webbläsaren på varje enhet, aldrig här.

Bygg om efter ändringar i Excelfilen (kör först `uv run skicka_till_garmin.py --alla` i Garmin-mappen):

    uv run bygg.py --publicera

`src/app.html` är appen, `src/plan.json` planen och `src/static/` ikonerna.
