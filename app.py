import os
import csv
import time
import io # Voor in-memory CSV
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session
import requests
from bs4 import BeautifulSoup
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.urandom(24) # Nodig voor flash messages
# app.config['UPLOAD_FOLDER'] = 'uploads' # Niet strikt nodig als we in-memory werken
# os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

BASE_URL = "https://www.bus.nl"
SEARCH_URL = f"{BASE_URL}/cgi-bin/search.pl"

# CSS Selectors (zoals opgegeven)
SELECTOR_ARTICLE_NAME = "div.dark_caption"
SELECTOR_PRICE = ".preis_list_preis"
# Verpakkingseenheid is wat lastiger, de selector span.light:nth-child(2) werkt mogelijk niet
# universeel. We proberen het te vinden op basis van de tekst "Verp.eenh.:".
# SELECTOR_PACKAGING_UNIT = "span.light:nth-child(2)" # Oorspronkelijke selector

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

def scrape_article_data(session_requests, article_number):
    """Scraapt data voor een specifiek artikelnummer."""
    print(f"Zoeken naar artikel: {article_number}")
    search_payload = {'suchtext': article_number}
    
    try:
        response = session_requests.post(SEARCH_URL, data=search_payload, headers=HEADERS, timeout=15)
        response.raise_for_status() # Werpt een error voor 4xx/5xx status codes
        soup = BeautifulSoup(response.content, 'html.parser')

        # De site kan direct naar een productpagina gaan of een lijst tonen.
        # We gaan ervan uit dat de selectors voor een lijstitem zijn.
        # Als er meerdere resultaten zijn, pakken we de eerste.
        
        # Probeer de hoofd productdetail container te vinden (als er een directe hit is)
        # of de eerste item in een lijst. Dit is een beetje een gok, bus.nl layout kan variëren.
        # De selectors waren meer voor een lijstweergave.
        
        article_container = soup.find('div', class_='art_liste_artikel_komplett') # Typische container voor een item in de lijst
        
        if not article_container:
            # Misschien een directe hit op een productpagina, andere structuur?
            # Voor nu houden we het bij de lijststructuur
            print(f"Geen duidelijke artikelcontainer gevonden voor {article_number} met selector 'art_liste_artikel_komplett'.")
            # Probeer de selectors op de hele pagina
            article_container = soup 

        name_element = article_container.select_one(SELECTOR_ARTICLE_NAME)
        article_name = name_element.get_text(strip=True) if name_element else "Niet gevonden"

        price_element = article_container.select_one(SELECTOR_PRICE)
        # Prijs kan complex zijn (bv. "vanaf € X,XX"). We nemen de meest prominente.
        price_text = "Niet gevonden"
        if price_element:
            # Soms staat er "ab " (vanaf) voor de prijs, die willen we niet
            price_text = price_element.get_text(separator=" ", strip=True).replace('ab ', '').replace('€', '').strip()
            # Als er meerdere prijzen zijn, pak de eerste
            if '\n' in price_text:
                price_text = price_text.split('\n')[0].strip()
        
        packaging_unit = "Niet gevonden"
        # Zoek naar de tekst "Verp.eenh.:" en pak de waarde erna.
        # Dit is robuuster dan een nth-child selector.
        pe_label_element = article_container.find(lambda tag: tag.name == 'span' and 'Verp.eenh.:' in tag.get_text())
        if pe_label_element and pe_label_element.next_sibling:
            # Het volgende sibling is vaak een NavigableString (de waarde)
            packaging_unit_raw = pe_label_element.next_sibling
            if packaging_unit_raw:
                 packaging_unit = str(packaging_unit_raw).strip()
        elif article_container.select_one("span.light:nth-child(2)"): # Fallback naar user's selector
             packaging_unit_el = article_container.select_one("span.light:nth-child(2)")
             if packaging_unit_el: # Check of het niet de Art.Nr. zelf is
                 if "Verp.eenh.:" not in packaging_unit_el.get_text(strip=True) and \
                    "Art.Nr.:" not in packaging_unit_el.get_text(strip=True):
                     packaging_unit = packaging_unit_el.get_text(strip=True)


        print(f"Resultaat: Naam: {article_name}, Prijs: {price_text}, Verp: {packaging_unit}")
        return {
            "artikelnummer": article_number,
            "naam": article_name,
            "prijs": price_text,
            "verpakkingseenheid": packaging_unit
        }

    except requests.exceptions.RequestException as e:
        print(f"Fout bij het opvragen van {article_number}: {e}")
        return {
            "artikelnummer": article_number,
            "naam": "Fout bij ophalen",
            "prijs": "N/A",
            "verpakkingseenheid": "N/A"
        }
    except Exception as e:
        print(f"Onverwachte fout bij verwerken {article_number}: {e}")
        return {
            "artikelnummer": article_number,
            "naam": "Verwerkingsfout",
            "prijs": "N/A",
            "verpakkingseenheid": "N/A"
        }

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        if 'csv_file' not in request.files:
            flash('Geen bestanddeel in het request', 'error')
            return redirect(request.url)
        
        file = request.files['csv_file']
        if file.filename == '':
            flash('Geen bestand geselecteerd', 'error')
            return redirect(request.url)

        if file and file.filename.endswith('.csv'):
            try:
                # Lees CSV direct vanuit de upload stream
                stream = io.StringIO(file.stream.read().decode("UTF-8"), newline=None)
                csv_input = csv.reader(stream)
                # Verwijder header als die er is (aanname: eerste rij is header of eerste artikelnummer)
                # We nemen gewoon de eerste kolom van elke rij
                article_numbers = []
                for row in csv_input:
                    if row: # Zorg dat de rij niet leeg is
                        article_numbers.append(row[0].strip())
                
                if not article_numbers:
                    flash('CSV-bestand is leeg of bevat geen artikelnummers in de eerste kolom.', 'error')
                    return redirect(request.url)

                scraped_data = []
                # Gebruik een sessie voor keep-alive en cookie handling
                with requests.Session() as session_requests:
                    for i, number in enumerate(article_numbers):
                        if number: # Sla lege nummers over
                            data = scrape_article_data(session_requests, number)
                            scraped_data.append(data)
                            time.sleep(0.5) # Wees vriendelijk voor de server
                            # Optioneel: update gebruiker over voortgang (lastiger zonder websockets/ajax)
                            print(f"Voortgang: {i+1}/{len(article_numbers)}")
                
                if not scraped_data:
                    flash('Geen data kunnen scrapen.', 'info')
                    return redirect(request.url)

                # Genereer CSV output in-memory
                output_csv_stream = io.StringIO()
                fieldnames = ['artikelnummer', 'naam', 'prijs', 'verpakkingseenheid']
                writer = csv.DictWriter(output_csv_stream, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(scraped_data)

                # Sla de CSV data op in de sessie om later te downloaden
                # Dit is beter dan op schijf opslaan in een stateless omgeving zoals Docker
                # (tenzij je volumes gebruikt, maar voor een tijdelijk bestand is dit prima)
                session['output_csv_data'] = output_csv_stream.getvalue()
                output_filename = f"bus_nl_scraped_data_{int(time.time())}.csv"
                session['output_filename'] = output_filename

                flash(f'Scrapen voltooid! {len(scraped_data)} artikelen verwerkt.', 'success')
                return render_template('index.html', download_file=output_filename)

            except UnicodeDecodeError:
                flash('Kon CSV-bestand niet lezen. Zorg ervoor dat het UTF-8 gecodeerd is.', 'error')
                return redirect(request.url)
            except Exception as e:
                flash(f'Er is een fout opgetreden: {e}', 'error')
                print(f"Fout tijdens verwerking: {e}") # Log de error ook server-side
                return redirect(request.url)
        else:
            flash('Ongeldig bestandsformaat. Upload a.u.b. een .csv bestand.', 'error')
            return redirect(request.url)
            
    return render_template('index.html')

@app.route('/download/<filename>')
def download_file(filename):
    if 'output_csv_data' in session and session.get('output_filename') == filename:
        csv_data = session.pop('output_csv_data') # Verwijder na download
        session.pop('output_filename')
        
        mem_file = io.BytesIO()
        mem_file.write(csv_data.encode('utf-8'))
        mem_file.seek(0) # Ga terug naar het begin van de BytesIO stream
        
        return send_file(
            mem_file,
            mimetype='text/csv',
            download_name=filename, # Gebruik de gegenereerde naam
            as_attachment=True
        )
    else:
        flash('Download niet beschikbaar of sessie verlopen.', 'error')
        return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
