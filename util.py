#!/usr/bin/env python3
from datetime import datetime, timezone
import json
import logging
import os.path
import random
import time
# import asyncio
import httpx
import riksdagen
from wikibaseintegrator import wbi_core, wbi_login

import config
# Constants
wd_prefix = "http://www.wikidata.org/entity/"


def yes_no_skip_question(message: str):
    # https://www.quora.com/
    # I%E2%80%99m-new-to-Python-how-can-I-write-a-yes-no-question
    # this will loop forever
    while True:
        answer = input(message + ' [(Y)es/(n)o/(s)kip this form]: ')
        if len(answer) == 0 or answer[0].lower() in ('y', 'n', 's'):
            if len(answer) == 0:
                return True
            elif answer[0].lower() == 's':
                return None
            else:
                # the == operator just returns a boolean,
                return answer[0].lower() == 'y'


def yes_no_question(message: str):
    # https://www.quora.com/
    # I%E2%80%99m-new-to-Python-how-can-I-write-a-yes-no-question
    # this will loop forever
    while True:
        answer = input(message + ' [Y/n]: ')
        if len(answer) == 0 or answer[0].lower() in ('y', 'n'):
            if len(answer) == 0:
                return True
            else:
                # the == operator just returns a boolean,
                return answer[0].lower() == 'y'


def sparql_query(query):
    # from https://stackoverflow.com/questions/55961615/
    # how-to-integrate-wikidata-query-in-python
    url = 'https://query.wikidata.org/sparql'
    r = httpx.get(url, params={'format': 'json', 'query': query})
    data = r.json()
    # pprint(data)
    results = data["results"]["bindings"]
    # pprint(results)
    if len(results) == 0:
        print(f"No {config.language} lexemes containing " +
              "both a sense, forms with " +
              "grammatical features and missing a usage example was found")
        exit(0)
    else:
        return results


def count_number_of_senses_with_P5137(lid):
    """Returns an int"""
    result = (sparql_query(f'''
    SELECT
    (COUNT(?sense) as ?count)
    WHERE {{
      VALUES ?l {{wd:{lid}}}.
      ?l ontolex:sense ?sense.
      ?sense skos:definition ?gloss.
      # Exclude lexemes without a linked QID from at least one sense
      ?sense wdt:P5137 [].
    }}'''))
    count = int(result[0]["count"]["value"])
    logging.debug(f"count:{count}")
    return count


def fetch_senses(lid):
    """Returns dictionary with numbers as keys and a dictionary as value with
    sense id and gloss"""
    # Thanks to Lucas Werkmeister https://www.wikidata.org/wiki/Q57387675 for
    # helping with this query.
    result = (sparql_query(f'''
    SELECT
    ?sense ?gloss
    WHERE {{
      VALUES ?l {{wd:{lid}}}.
      ?l ontolex:sense ?sense.
      ?sense skos:definition ?gloss.
      # Get only the swedish gloss, exclude otherwise
      FILTER(LANG(?gloss) = "{config.language_code}")
      # Exclude lexemes without a linked QID from at least one sense
      ?sense wdt:P5137 [].
    }}'''))
    senses = {}
    number = 1
    for row in result:
        senses[number] = {
            "sense_id": row["sense"]["value"].replace(wd_prefix, ""),
            "gloss": row["gloss"]["value"]
        }
        number += 1
    logging.debug(f"senses:{senses}")
    return senses


def fetch_lexeme_forms():
    return sparql_query(f'''
    SELECT DISTINCT
    ?l ?form ?word ?catLabel
    WHERE {{
      ?l a ontolex:LexicalEntry; dct:language wd:{config.language_qid}.
      VALUES ?excluded {{
        # exclude affixes and interfix
        wd:Q62155 # affix
        wd:Q134830 # prefix
        wd:Q102047 # suffix
        wd:Q1153504 # interfix
      }}
      MINUS {{?l wdt:P31 ?excluded.}}
      ?l wikibase:lexicalCategory ?cat.

      # We want only lexemes with both forms and at least one sense
      ?l ontolex:lexicalForm ?form.
      ?l ontolex:sense ?sense.

      # Exclude lexemes without a linked QID from at least one sense
      ?sense wdt:P5137 [].

      # This remove all lexemes with at least one example which is not
      # ideal
      MINUS {{?l wdt:P5831 ?example.}}
      ?form wikibase:grammaticalFeature [].
      # We extract the word of the form
      ?form ontolex:representation ?word.
      SERVICE wikibase:label
      {{ bd:serviceParam wikibase:language "en". }}
    }}
    limit {config.sparql_results_size}
    offset {config.sparql_offset}
    ''')


def extract_data(result):
    lid = result["l"]["value"].replace(
        wd_prefix, ""
    )
    form_id = result["form"]["value"].replace(
        wd_prefix, ""
    )
    word = result["word"]["value"]
    word_spaces = " " + word + " "
    word_angle_parens = ">" + word + "<"
    category = result["catLabel"]["value"]
    return dict(
        lid=lid,
        form_id=form_id,
        word=word,
        word_spaces=word_spaces,
        word_angle_parens=word_angle_parens,
        category=category
    )


# async def async_fetch_from_url(url):
#     async with httpx.AsyncClient() as client:
#         response = await client.get(url)
#         return response


def add_usage_example(
        document_id=None,
        sentence=None,
        lid=None,
        form_id=None,
        sense_id=None,
        word=None,
        publication_date=None,
):
    # Use WikibaseIntegrator aka wbi to upload the changes in one edit
    if publication_date is not None:
        publication_date = datetime.fromisoformat(publication_date)
    else:
        print("Publication date of document {document_id} " +
              "is missing. We have no fallback for that at the moment. " +
              "Abort adding usage example.")
        return False
    link_to_form = wbi_core.Form(
        prop_nr="P5830",
        value=form_id,
        is_qualifier=True
    )
    link_to_sense = wbi_core.Sense(
        prop_nr="P6072",
        value=sense_id,
        is_qualifier=True
    )
    reference = [
        wbi_core.ItemID(
            prop_nr="P248",  # Stated in Riksdagen open data portal
            value="Q21592569",
            is_reference=True
        ),
        wbi_core.ExternalID(
            prop_nr="P8433",  # Riksdagen Document ID
            value=document_id,
            is_reference=True
        ),
        wbi_core.Time(
            prop_nr="P813",  # Fetched today
            time=datetime.utcnow().replace(
                tzinfo=timezone.utc
            ).replace(
                hour=0,
                minute=0,
                second=0,
            ).strftime("+%Y-%m-%dT%H:%M:%SZ"),
            is_reference=True,
        ),
        wbi_core.Time(
            prop_nr="P577",  # Publication date
            time=publication_date.strftime("+%Y-%m-%dT00:00:00Z"),
            is_reference=True,
        )
    ]
    # This is th usage example statement
    claim = wbi_core.MonolingualText(
        sentence,
        "P5831",
        language=config.language_code,
        # Add qualifiers
        qualifiers=[link_to_form, link_to_sense],
        # Add reference
        references=[reference],
    )
    if config.debug_json:
        logging.debug(f"claim:{claim.get_json_representation()}")
    item = wbi_core.ItemEngine(
        data=[claim], append_value=["P5831"], item_id=lid,
    )
    if config.debug_json:
        print(item.get_json_representation())
    if config.login_instance is None:
        # Authenticate with WikibaseIntegrator
        print("Logging in with Wikibase Integrator")
        config.login_instance = wbi_login.Login(
            user=config.username, pwd=config.password
        )
    result = item.write(
        config.login_instance,
        edit_summary="Added usage example with [[Wikidata:LexUse]]"
    )
    if config.debug_json:
        logging.debug(f"result from WBI:{result}")
    return result


def count_words(string):
    # from https://www.pythonpool.com/python-count-words-in-string/
    return(len(string.strip().split(" ")))


def prompt_choose_sense(senses):
    """Returns a dictionary with sense_id -> sense_id
    and gloss -> gloss or False"""
    # from https://stackoverflow.com/questions/23294658/
    # asking-the-user-for-input-until-they-give-a-valid-response
    while True:
        try:
            options = ("Please choose the correct sense corresponding " +
                       "to the meaning in the usage example")
            number = 1
            # Put each key -> value into a new nested dictionary
            for sense in senses:
                options += f"\n{number}) {senses[number]['gloss']}"
                number += 1
            options += "\nPlease input a number or 0 to cancel: "
            choice = int(input(options))
        except ValueError:
            print("Sorry, I didn't understand that.")
            # better try again... Return to the start of the loop
            continue
        else:
            logging.debug(f"length_of_senses:{len(senses)}")
            if choice > 0 and choice <= len(senses):
                return {
                    "sense_id": senses[choice]["sense_id"],
                    "gloss": senses[choice]["gloss"]
                }
            else:
                print("Cancelled adding this sentence.")
                return False


def add_to_watchlist(lid):
    # Get session from WBI, it cannot be None because this comes after adding
    # an
    # usage example with WBI.
    session = config.login_instance.get_session()
    # adapted from https://www.mediawiki.org/wiki/API:Watch
    url = "https://www.wikidata.org/w/api.php"
    params_token = {
        "action": "query",
        "meta": "tokens",
        "type": "watch",
        "format": "json"
    }

    result = session.get(url=url, params=params_token)
    data = result.json()

    csrf_token = data["query"]["tokens"]["watchtoken"]

    params_watch = {
        "action": "watch",
        "titles": "Lexeme:" + lid,
        "format": "json",
        "formatversion": "2",
        "token": csrf_token,
    }

    result = session.post(
        url, data=params_watch
    )
    if config.debug_json:
        print(result.text)
    print(f"Added {lid} to your watchlist")


def prompt_sense_approval(sentence=None, data=None):
    """Prompts for validating that we have a sense matching the use example
    return dictionary with sense_id and sense_gloss if approved else False"""
    lid = data["lid"]
    # This returns a tuple if one sense or a dictionary if multiple senses
    senses = fetch_senses(lid)
    number_of_senses = len(senses)
    logging.debug(f"number_of_senses:{number_of_senses}")
    if number_of_senses > 0:
        if number_of_senses == 1:
            gloss = senses[1]["gloss"]
            if yes_no_question("Found only one sense. " +
                               "Does this example fit the following " +
                               f"gloss? \n'{gloss}'"):
                return {
                    "sense_id": senses[1]["sense_id"],
                    "sense_gloss": gloss
                }
            else:
                word = data['word']
                print("Cancelled adding sentence as it does not match the " +
                      "only sense currently present. \nLexemes are " +
                      "entirely dependent on good quality QIDs. \n" +
                      "Please add labels " +
                      "and descriptions to relevant QIDs and then use " +
                      "MachtSinn to add " +
                      "more senses to lexemes by matching on QID concepts " +
                      "with similar labels and descriptions in the lexeme " +
                      "language." +
                      f"\nSearch for {word} in Wikidata: " +
                      "https://www.wikidata.org/w/index.php?" +
                      f"search={word}&title=Special%3ASearch&" +
                      "profile=advanced&fulltext=0&" +
                      "advancedSearch-current=%7B%7D&ns0=1")
                time.sleep(5)
                return False
        else:
            print(f"Found {number_of_senses} senses.")
            sense = False
            # TODO check that all senses has a gloss matching the language of
            # the example
            sense = prompt_choose_sense(senses)
            if sense:
                logging.debug("sense was accepted")
                return {
                    "sense_id": sense["sense_id"],
                    "sense_gloss": sense["gloss"]
                }
            else:
                return False
    else:
        # Check if any suitable senses exist
        count = (count_number_of_senses_with_P5137("L35455"))
        if count > 0:
            print("{language.title()} gloss is missing for {count} sense(s)" +
                  ". Please fix it manually here: " +
                  f"{wd_prefix + lid}")
            time.sleep(5)
            return False
        else:
            logging.debug("no senses this should never be reached " +
                          "if the sparql result was sane")
            return False


def get_sentences_from_apis(result):
    data = extract_data(result)
    form_id = data["form_id"]
    word = data["word"]
    print(f"Trying to find examples for the {data['category']} lexeme " +
          f"form: {word} with id: {form_id}")
    # Riksdagen API
    riksdagen.get_records(data)
    # TODO K-samsök
    # TODO Europarl corpus


def present_sentence(
        data,
        sentence,
        document_id,
        date
):
    """Return True, False or None (skip)"""
    word_count = count_words(sentence)
    result = yes_no_skip_question(
            f"Found the following sentence with {word_count} " +
            "words. Is it suitable as a usage example " +
            f"for the form '{data['word']}'? \n" +
            f"'{sentence}'"
    )
    if result:
        selected_sense = prompt_sense_approval(
            sentence=sentence,
            data=data
        )
        if selected_sense is not False:
            lid = data["lid"]
            sense_id = selected_sense["sense_id"]
            sense_gloss = selected_sense["sense_gloss"]
            if (sense_id is not None and sense_gloss is not None):
                result = False
                result = add_usage_example(
                    document_id=document_id,
                    sentence=sentence,
                    lid=lid,
                    form_id=data["form_id"],
                    sense_id=sense_id,
                    word=data["word"],
                    publication_date=date,
                )
                if result:
                    print("Successfully added usage example " +
                          f"to {wd_prefix + lid}")
                    add_to_watchlist(lid)
                    return True
                else:
                    return False
            else:
                return False
    elif result is None:
        # None means skip
        return None
    else:
        return False


def save_to_exclude_list(data: dict):
    # date, lid and lang
    if data is None:
        print("Error. Data was None")
        exit(1)
    logging.debug(f"data to exclude:{data}")
    form_id = data["form_id"]
    word = data["word"]
    form_data = dict(
        word=word,
        date=datetime.now().isoformat(),
        lang=config.language_code,
    )
    logging.debug(f"adding:{form_id}:{form_data}")
    if os.path.isfile('exclude_list.json'):
        logging.debug("File exist")
        # Read the file
        with open('exclude_list.json', 'r', encoding='utf-8') as myfile:
            json_data = myfile.read()
        if len(json_data) > 0:
            with open('exclude_list.json', 'w', encoding='utf-8') as myfile:
                # parse file
                exclude_list = json.loads(json_data)
                exclude_list[form_id] = form_data
                logging.debug(f"dumping altered list:{exclude_list}")
                json.dump(exclude_list, myfile, ensure_ascii=False)
        else:
            print("Error. json data is null.")
            exit(1)
    else:
        logging.debug("File not exist")
        # Create the file
        with open("exclude_list.json", "w", encoding='utf-8') as outfile:
            # Create new file with dict and item
            exclude_list = {}
            exclude_list[form_id] = form_data
            logging.debug(f"dumping:{exclude_list}")
            json.dump(exclude_list, outfile, ensure_ascii=False)


def process_result(result, data):
    # ask to continue
    # if yes_no_question(f"\nWork on {data['word']}?"):
    # This dict holds the sentence as key and
    # riksdagen_document_id as value
    sentences_and_result_data = get_sentences_from_apis(result)
    if sentences_and_result_data is not None:
        # Sort so that the shortest sentence is first
        sorted_sentences = sorted(
            sentences_and_result_data, key=len,
        )
        count = 1
        # Loop through sentence list
        for sentence in sorted_sentences:
            print("Presenting sentence " +
                  f"{count}/{len(sorted_sentences)}")
            result_data = sentences_and_result_data[sentence]
            document_id = result_data["document_id"]
            date = result_data["date"]
            if config.debug_sentences:
                print("with document_id: " +
                      f"{document_id} from {date}")
            result = present_sentence(
                data,
                sentence,
                document_id,
                date
            )
            count += 1
            # Break out of the for loop by returning early because one
            # example was already choosen for this result or if the form
            # was skipped
            if result or result is None:
                # Add to temporary exclude_list
                logging.debug("adding to exclude list after presentation")
                save_to_exclude_list(data)
                # break
                return
    else:
        print("Added to excludelist because of no " +
              "suitable sentences were found")
        save_to_exclude_list(data)


def in_exclude_list(data: dict):
    # Check if in exclude_list
    if os.path.isfile('exclude_list.json'):
        logging.debug("Looking up in exclude list")
        # Read the file
        with open('exclude_list.json', 'r', encoding='utf-8') as myfile:
            json_data = myfile.read()
            # parse file
            exclude_list = json.loads(json_data)
            lid = data["lid"]
            for form_id in exclude_list:
                form_data = exclude_list[form_id]
                logging.debug(f"found:{form_data}")
                if (
                        # TODO check the date also
                        lid == form_id
                        and config.language_code == form_data["lang"]
                ):
                    logging.debug("Match found")
                    return True
        # Not found in exclude_list
        return False
    else:
        # No exclude_list
        return False


def process_lexeme_data(results):
    """Go through the SPARQL results randomly"""
    words = []
    for result in results:
        data = extract_data(result)
        words.append(data["word"])
    print(f"Got {len(words)} suitable forms from Wikidata")
    logging.debug(f"words:{words}")
    # Go through the results at random
    print("Going through the list of forms at random.")
    # from http://stackoverflow.com/questions/306400/ddg#306417
    earlier_choices = []
    while (True):
        if len(earlier_choices) == config.sparql_results_size:
            # We have gone checked all results now
            # TODO offer to fetch more
            print("No more results. Run the script again to continue")
            exit(0)
        else:
            result = random.choice(results)
            # Prevent running more than once for each result
            if result not in earlier_choices:
                earlier_choices.append(result)
                data = extract_data(result)
                word = data['word']
                logging.debug(f"random choice:{word}")
                if in_exclude_list(data):
                    # Skip if found in the exclude_list
                    logging.debug(
                        f"Skipping result {word} found in exclude_list",
                    )
                    continue
                else:
                    # not in exclude_list
                    logging.debug(f"processing:{word}")
                    process_result(result, data)


def introduction():
    if yes_no_question("This script enables you to " +
                       "semi-automatically add usage examples to " +
                       "lexemes with both good senses and forms " +
                       "(with P5137 and grammatical features respectively). " +
                       "\nPlease pay attention to the lexical " +
                       "category of the lexeme. \nAlso try " +
                       "adding only short and concise " +
                       "examples to avoid bloat and maximise " +
                       "usefullness. \nThis script adds edited " +
                       "lexemes (indefinitely) to your watchlist. " +
                       "\nContinue?"):
        return True
    else:
        return False
