def split_regular_gtt(intents):
    regular = [i for i in intents if (i.gtt or "").upper() != "YES"]
    gtts    = [i for i in intents if (i.gtt or "").upper() == "YES"]
    return regular, gtts
