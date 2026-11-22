""






import re

_READERS = {"cat", "ls", "head", "tail", "wc", "grep", "egrep", "fgrep", "rg", "jq", "find", "sed", "stat", "du", "cut",
            "tr", "strings", "sort", "sqlite3", "cd", "pwd"}
_JOINS = {"|", "&&", "||", ";"}
_OPERATORS = set("|&;<>()")
_EXPANDS = re.compile(r"`|\$|<\(|>\(|<<|\n")
_QUIET = re.compile(r"\s+2>\s*/dev/null(?=\s|$)|\s+2>&1(?=\s|$)")
_FIND_ACTS = {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"}
_SED_PRINT = re.compile(r"^(?:\d+|\$)(?:,(?:\d+|\$))?p$")

_SORT_READS = re.compile(r"^-(?:[bdfghinrRuVM]+|[kt].*)$")
_RG_RUNS = ("--pre", "--hostname-bin")

_SQL_READ = re.compile(r"^\s*(?:select|with|pragma\s+table_info|explain)\b", re.I)
_SQL_ACTS = re.compile(r"\b(?:writefile|edit|load_extension|attach)\b", re.I)


_SQLITE_OPTS = {"-readonly", "-batch", "-json", "-csv", "-header", "-noheader", "-column", "-line", "-list", "-box",
                "-table", "-markdown"}
_SQLITE_RO_URI = re.compile(r"file:[^?]*\?mode=ro")


def _sqlite_reads(args: list[str]) -> bool:
    words = [a for a in args if not a.startswith("-")]
    if set(args) - set(words) - _SQLITE_OPTS or len(words) < 2:
        return False
    if "-readonly" not in args and not _SQLITE_RO_URI.fullmatch(words[0]):
        return False

    return all(_SQL_READ.match(sql) and not _SQL_ACTS.search(sql) and ";" not in sql.rstrip().rstrip(";")
               for sql in words[1:])


def _part_reads(words: list[str]) -> bool:

    if not words or words[0] not in _READERS:
        return False
    name, args = words[0], words[1:]
    if name == "find":
        return not _FIND_ACTS & set(args)
    if name == "sed":

        return (args[:1] == ["-n"] and len(args) >= 2 and bool(_SED_PRINT.match(args[1]))
                and not any(a.startswith("-") for a in args[2:]))
    if name == "sort":
        return all(_SORT_READS.match(a) for a in args if a.startswith("-") and a != "-")
    if name == "rg":
        return not any(a.startswith(_RG_RUNS) for a in args)
    if name == "sqlite3":
        return _sqlite_reads(args)
    return True


def _split(command: str) -> list[list[str]] | None:
    ""




    parts, words, word, op = [], [], None, ""
    chars = iter(command)
    for ch in chars:
        if ch in _OPERATORS:
            op += ch
            if word is not None:
                words.append(word)
                word = None
            continue
        if op:
            if op not in _JOINS:
                return None
            parts.append(words)
            words, op = [], ""
        if ch in " \t":
            if word is not None:
                words.append(word)
                word = None
        elif ch == "'":
            word = word or ""
            for quoted in chars:
                if quoted == "'":
                    break
                word += quoted
            else:
                return None
        elif ch == '"':
            word = word or ""
            for quoted in chars:
                if quoted == '"':
                    break
                if quoted == "\\":
                    following = next(chars, None)
                    if following is None:
                        return None
                    quoted = following if following in '"\\' else quoted + following
                word += quoted
            else:
                return None
        elif ch == "\\":
            escaped = next(chars, None)
            if escaped is None:
                return None
            word = (word or "") + escaped
        elif ch in "{}" or (ch in "*?[" and (word or "").startswith("-")):
            return None
        else:
            word = (word or "") + ch
    if op and op not in _JOINS:
        return None
    if word is not None:
        words.append(word)
    if op:
        parts.append(words)
        words = []
    if words or not parts:
        parts.append(words)
    return parts


def only_reads(command: str) -> bool:
    ""
    if not command.strip() or _EXPANDS.search(command):
        return False
    parts = _split(_QUIET.sub("", command))
    return parts is not None and all(_part_reads(part) for part in parts)
