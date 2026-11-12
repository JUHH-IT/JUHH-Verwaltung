"""Geld- und Datumsformate. Alle Geldbeträge werden als ganze Cent geführt."""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

LIMIT = Decimal('100000000')
_THOUSANDS_DOT = re.compile(r'\d{1,3}(\.\d{3})+')
_THOUSANDS_COMMA = re.compile(r'\d{1,3}(,\d{3})+')


def _cents(n):
    if not n.is_finite() or n != n.quantize(Decimal('.01')):
        raise ValueError('Beträge benötigen höchstens zwei Nachkommastellen.')
    if abs(n) > LIMIT:
        raise ValueError('Betrag ist zu groß.')
    return int(n * 100)


def money(value):
    """Liest einen Betrag im deutschen oder englischen Format und liefert Cent.

    „1.500“ und „1.500,00“ sind 1.500 €, „12,5“ und „12.50“ sind 12,50 €. Uneindeutige
    Schreibweisen wie „1,234“ werden abgelehnt statt still falsch gelesen.
    """
    if isinstance(value, bool):
        raise ValueError('Ungültiger Geldbetrag.')
    s = str(value).strip()
    for token in ('€', 'EUR', 'eur', ' ', ' ', "'"):
        s = s.replace(token, '')
    s = s.replace('−', '-')
    negative = False
    if len(s) > 1 and s.endswith('-'):
        negative, s = True, s[:-1]
    if s[:1] in '+-' and s:
        negative, s = negative or s[0] == '-', s[1:]
    if not s:
        raise ValueError('Ungültiger Geldbetrag.')
    if ',' in s and '.' in s:
        s = s.replace('.', '').replace(',', '.') if s.rfind(',') > s.rfind('.') else s.replace(',', '')
    elif ',' in s:
        if s.count(',') > 1:
            if not _THOUSANDS_COMMA.fullmatch(s):
                raise ValueError('Ungültiger Geldbetrag.')
            s = s.replace(',', '')
        else:
            s = s.replace(',', '.')
    elif _THOUSANDS_DOT.fullmatch(s):
        s = s.replace('.', '')
    if not re.fullmatch(r'\d+(\.\d+)?', s):
        raise ValueError('Ungültiger Geldbetrag.')
    try:
        n = Decimal(s)
    except InvalidOperation:
        raise ValueError('Ungültiger Geldbetrag.')
    cents = _cents(n)
    return -cents if negative else cents


def cents_from_number(value):
    """Zahl aus einer Tabellenzelle (float) exakt in Cent umrechnen."""
    if isinstance(value, bool):
        raise ValueError('Ungültiger Geldbetrag.')
    return _cents(Decimal(repr(float(value))))


def iso(value):
    for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%d.%m.%y'):
        try:
            return datetime.strptime(str(value).strip(), fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError('Datum muss JJJJ-MM-TT oder TT.MM.JJJJ entsprechen.')


def euro(cents):
    """Cent → „1.234,50 €“ (für PDF und E-Mail)."""
    sign = '-' if cents < 0 else ''
    text = f'{abs(cents) / 100:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return f'{sign}{text} €'


def german_date(value):
    """„2026-10-28“ → „28.10.2026“."""
    return datetime.strptime(value, '%Y-%m-%d').strftime('%d.%m.%Y') if value else ''


def add_months(day, months):
    """„2024-02-29“ + 36 Monate → „2027-02-28“ (Monatsende, wenn der Tag im Zielmonat fehlt)."""
    d = date.fromisoformat(day)
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, last)).isoformat()


def iban_compact(value):
    return re.sub(r'\s+', '', str(value or '')).upper()


def iban_valid(value):
    """Prüfziffer nach ISO 13616 (Modulo 97)."""
    s = iban_compact(value)
    if not re.fullmatch(r'[A-Z]{2}\d{2}[A-Z0-9]{11,30}', s):
        return False
    digits = ''.join(str(int(c, 36)) for c in s[4:] + s[:4])
    return int(digits) % 97 == 1


def iban_pretty(value):
    s = iban_compact(value)
    return ' '.join(s[i:i + 4] for i in range(0, len(s), 4))


_EMAIL = re.compile(r'[^@\s,;<>"]+@[^@\s,;<>"]+\.[A-Za-z]{2,}')


def email_address(value, required=False):
    """Prüft eine einzelne E-Mail-Adresse; liefert sie bereinigt oder '' (wenn erlaubt)."""
    s = str(value or '').strip()
    if not s:
        if required:
            raise ValueError('E-Mail-Adresse fehlt.')
        return ''
    if not _EMAIL.fullmatch(s) or len(s) > 254:
        raise ValueError(f'Ungültige E-Mail-Adresse: {s}')
    if not s.isascii():
        raise ValueError(f'E-Mail-Adressen mit Umlauten oder Sonderzeichen werden nicht unterstützt: {s}')
    return s
