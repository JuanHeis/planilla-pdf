"""Small, local post-correction model for printed OCR labels."""

import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher


DEFAULT_GLOSSARY = tuple("""
    apellido apellidos nombre nombres documento identidad argentina formulario
    solicitud inscripción modificación datos recepción trámite espontáneo
    convocatoria convocatorio oficio identificación nacimiento fallecimiento sexo
    fecha nacionalidad extranjero extranjería domicilio calle número piso
    departamento depto código postal localidad provincia municipio teléfono celular
    correo electrónico email contacto titular responsable representante vínculo
    parentesco cuil cuit dni estado civil educación establecimiento matrícula
    estudios firma aclaración certificación observaciones empleador trabajo ingresos
    familiar familia prestación asignación dependencia residencia
    paterno materno casada referenciales fiscal real partido actividad
    laboral servicio personal beneficiario certificado autorización declaración
    jurada profesional institución nacional derecho persona
    vencimiento origen clase pasaporte argentino
    testigo solicitante relación adjuntar ocupación
    comercial categoría detalle referencia comunicación información público pública
""".split())

_WORD = re.compile(r"\b[^\W\d_]+\b")


def _key(value):
    return unicodedata.normalize("NFC", value).casefold()


def _copy_case(source, target):
    if source.isupper():
        return target.upper()
    if source.istitle():
        return target.title()
    if source.islower():
        return target.lower()
    return target


def _drop_one(word):
    for index in range(len(word)):
        yield word[:index] + word[index + 1:]


class OcrCorrector:
    """Compare the unchanged OCR, a glossary, and supervised edit suggestions."""

    def __init__(self, examples=(), glossary=None):
        words = DEFAULT_GLOSSARY if glossary is None else glossary
        self._words = {_key(word) for term in words for word in _WORD.findall(term)}
        self._lengths = defaultdict(set)
        self._longer = defaultdict(set)
        for word in self._words:
            self._lengths[len(word)].add(word)
            for shortened in _drop_one(word):
                self._longer[shortened].add(word)

        self._confirmed = defaultdict(dict)
        self._confirmed_words = set()
        training_pairs = set()
        for row in examples:
            original, corrected = row["original"].strip(), row["corrected"].strip()
            if not original or len(original) > 160 or len(corrected) > 160:
                continue
            raw_key, clean_key = _key(original), _key(corrected)
            self._confirmed[raw_key].setdefault(clean_key, corrected)
            raw_words = _WORD.findall(raw_key)
            clean_words = _WORD.findall(clean_key)
            if len(raw_words) == len(clean_words):
                for source, target in zip(raw_words, clean_words):
                    if source == target:
                        self._confirmed_words.add(source)
                    elif 4 <= len(source) <= 40 and target in self._words:
                        training_pairs.add((source, target))

        edits = Counter()
        for source, target in training_pairs:
            pair_edits = set()
            for opcode, left, right, start, end in SequenceMatcher(
                None, source, target, autojunk=False
            ).get_opcodes():
                if opcode in {"replace", "delete"} and 1 <= right - left <= 3 and end - start <= 3:
                    pair_edits.add((source[left:right], target[start:end]))
            edits.update(pair_edits)
        self._edits = tuple(edit for edit, support in edits.items() if support >= 2)

    def suggest(self, text, mode="model"):
        """Return a string suggestion, or the original when evidence is weak."""
        if mode not in {"raw", "glossary", "model"}:
            raise ValueError("Unknown OCR correction mode.")
        if mode == "raw" or not text:
            return text

        if mode == "model":
            stripped = text.strip()
            outcomes = self._confirmed.get(_key(stripped))
            if outcomes:
                if len(outcomes) != 1 or _key(stripped) in outcomes:
                    return text
                correction = next(iter(outcomes.values()))
                if correction and not any(char.isdigit() for char in stripped + correction):
                    return text.replace(stripped, _copy_case(stripped, correction), 1)
                return text

        return _WORD.sub(lambda match: self._suggest_word(match[0], mode), text)

    def _suggest_word(self, word, mode):
        source = _key(word)
        if source in self._words or len(source) > 40:
            return word
        if mode == "model" and source in self._confirmed_words:
            return word

        if mode == "model" and len(source) >= 4:
            candidates = set()
            for noisy, clean in self._edits:
                start = source.find(noisy)
                while start >= 0:
                    candidate = source[:start] + clean + source[start + len(noisy):]
                    if candidate in self._words:
                        candidates.add(candidate)
                    start = source.find(noisy, start + 1)
            if candidates:
                return _copy_case(word, next(iter(candidates))) if len(candidates) == 1 else word

        if len(source) < 5:
            return word
        neighbors = self._longer.get(source, set()) | (set(_drop_one(source)) & self._words)
        neighbors.update(
            candidate for candidate in self._lengths.get(len(source), ())
            if sum(left != right for left, right in zip(source, candidate)) == 1
        )
        return _copy_case(word, next(iter(neighbors))) if len(neighbors) == 1 else word
