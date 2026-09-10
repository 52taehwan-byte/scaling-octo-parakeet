"""Conservative attendance extraction from explicit work statements."""
import re


def attendance_mentions(text, names=()):
    found = {}
    for sentence in re.split(r"[\n.!?]", text or ""):
        if not re.search(r"일했|작업했|투입했|함께 일한 사람", sentence):
            continue
        if re.search(r"안\s*왔|못\s*왔|안\s*일|일하지|내일|예정|견적", sentence):
            continue
        for name in dict.fromkeys(("아마라", "니마", *names)):
            if name in sentence:
                found[name] = {"name": name, "count": 1}
        for match in re.finditer(r"(우즈벡|몽골|작업자|조공|기공)\s*(\d+)\s*명", sentence):
            label, count = match.groups()
            found[label] = {"name": label, "count": int(count)}
    return list(found.values())


def labor_forecast(text, day, rates):
    active = {}
    for rate in rates:
        if rate['effective_on'] <= day:
            old = active.get(rate['name'])
            if old is None or (rate['effective_on'], rate['saved_at']) >= (old['effective_on'], old['saved_at']):
                active[rate['name']] = rate
    workers = attendance_mentions(text, [r['name'] for r in rates])
    for worker in workers:
        name = worker['name']
        sentences = [s for s in re.split(r'[\n.!?]', text or '') if name in s and re.search(r'일했|작업했|투입했', s)]
        units = set()
        for sentence in sentences:
            if '반나절' in sentence and ('하루' in sentence or '종일' in sentence):
                units.update((0.5, 1))
                continue
            if '반나절' in sentence:
                units.add(0.5)
            elif '하루' in sentence or '종일' in sentence:
                units.add(1)
        unit = next(iter(units)) if len(units) == 1 else None
        rate = active.get(name)
        worker.update(rate=rate['amount'] if rate else None, units=unit,
                      estimate=int(rate['amount'] * unit * worker['count']) if rate and unit else None)
    return workers
