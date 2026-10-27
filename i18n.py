import os
import csv
import re
import config


_ESCAPE_RE = re.compile(r"\\(n|r|t|'|\\)")
_ESCAPES = {
	"n": "\n",
	"r": "\r",
	"t": "\t",
	"'": "'",
	"\\": "\\",
}


def _unescape(value):
	if not value:
		return value
	return _ESCAPE_RE.sub(lambda m: _ESCAPES[m.group(1)], value)


class I18n:
	def __init__(self, locales_path=None, default_language=None, encoding="utf-8"):
		if not locales_path:
			locales_path = os.path.join(os.path.dirname(
				os.path.abspath(__file__)), 'locales.csv')

		self.locales_path = locales_path
		self.encoding = encoding

		self._language_indexes = {}
		self._values = {}

		self._load_locales()

		if not self.languages:
			raise ValueError("No languages found in locales file")

		self.default_language = default_language or self.languages[0]

		if self.default_language not in self.languages:
			for language in self.languages:
				if language.startswith(self.default_language):
					self.default_language = language
					break
			else:
				raise ValueError(f"Unknown language: {self.default_language}")

	def _load_locales(self):
		with open(self.locales_path, newline="", encoding=self.encoding) as f:
			reader = csv.DictReader(f)
			fields = reader.fieldnames or []

			if "key" not in fields:
				raise ValueError("CSV must contain a 'key' column")

			self._language_indexes = {
				code: index for index, code in enumerate([field for field in fields if field != "key"])
			}

			for row in reader:
				key = row["key"]
				self._values[key] = tuple(
					_unescape(row.get(code)) for code in self.languages
				)

	def match(self, code):
		if not code:
			return None
		code = str(code).strip().replace("_", "-").lower()
		by_lower = {lang.lower(): lang for lang in self._language_indexes}
		if code in by_lower:
			return by_lower[code]
		# 'ru' -> первый 'ru-*' в файле (порядок колонок locales.csv)
		base = code.split("-")[0]
		return next((lang for key, lang in by_lower.items() if key.split("-")[0] == base), None)

	def resolve(self, code=None):
		return self.match(code) or self.default_language

	def _index(self, code=None):
		return self._language_indexes[self.resolve(code) if code else self.default_language]

	def _get_value(self, key, index):
		values = self._values.get(key)
		if values is None:
			return key  # нет такого ключа — лучше показать ключ, чем уронить запрос
		# Пустая ячейка -> язык по умолчанию -> первый язык файла.
		default_index = self._language_indexes[self.default_language]
		return values[index] or values[default_index] or values[0] or key

	def __getitem__(self, key):
		return self._get_value(key, self._index())

	def get(self, key, code=None, **kwargs):
		value = self._get_value(key, self._index(code))
		return value.format(**kwargs) if kwargs else value

	def values(self, keys=None, code=None):
		index = self._index(code)
		keys = self._values if keys is None else keys

		return {
			key: self._get_value(key, index)
			for key in keys
		}

	@property
	def languages(self):
		return tuple(self._language_indexes.keys())


i18n = I18n(default_language=config.DEFAULT_LANGUAGE)
