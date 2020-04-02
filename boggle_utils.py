import math
import random
import json
from dataclasses import dataclass, field
from itertools import islice, chain
import os
import logging
import glob
import re
import unidecode

logger = logging.getLogger(__name__)


def roll(seed, *, dice_config, board_dims=None):
    num_dice = len(dice_config)
    if board_dims is not None:
        rows, cols = board_dims
        if rows * cols != num_dice:
            raise ValueError(
                f"Board dimensions {rows, cols} not compatible with "
                f"number of dice {num_dice}."
            )
    else:
        dice_sq = round(math.sqrt(num_dice))
        if dice_sq * dice_sq != num_dice:
            raise ValueError(
                f"The number of dice {num_dice} is not a perfect square. "
                "Set board_dims or provide a square dice count"
            )
        rows = cols = dice_sq

    rng = random.Random(seed)
    random_dice = rng.sample(dice_config, len(dice_config))
    flat_board = [
        die[rng.randrange(len(die))] for die in random_dice
    ]
    board_iter = iter(flat_board)
    board = [[ch for ch in islice(board_iter, cols)] for _ in range(rows)]
    return (rows, cols), board


def clean_word(word):
    return unidecode.unidecode(word).upper()


class BoggleWord:
    """
    Utility wrapper to force Boggle words to be upper case, strip diacritics and
    compare with the QU -> Q substitution.
    """
    def __init__(self, word):
        self.word = word = clean_word(word)
        self._qnormd = word.replace('QU', 'Q')

    def __str__(self):
        return self.word

    def __eq__(self, other):
        return self._qnormd == self._qnormd

    def __hash__(self):
        return hash(self._qnormd)


@dataclass(frozen=True)
class Letter:
    i: int
    j: int
    label: str
    seen: set = field(default_factory=set)
    path: list = field(default_factory=list)

    def __str__(self):
        return ''.join(lett.label for lett in self.path) + self.label

    def __repr__(self):
        return '<%s (%d,%d)>' % (str(self), self.i, self.j)


def paths(word, board):
    board_rows = len(board)
    if board_rows == 0:
        raise ValueError
    board_cols = len(board[0])
    if len(word) < 3 or len(word) > 16:
        return iter([])

    initial_points = [
        Letter(i=i, j=j, label=ch)
        for i, row in enumerate(board)
        for j, ch in enumerate(row)
        if ch == word[0]
    ]

    def branch(letter: Letter, next_ch: str):
        moves = [
            (letter.i + dy, letter.j + dx)
            for dx in range(-1, 2)
            for dy in range(-1, 2)
            if dx or dy
        ]

        for m in moves:
            i, j = m
            if 0 <= i < board_rows and 0 <= j < board_cols and \
                    m not in letter.seen and board[i][j] == next_ch:
                node = Letter(
                    i=i, j=j, label=next_ch,
                    seen={(letter.i, letter.j), *letter.seen},
                    path=[*letter.path, letter]
                )
                yield node

    def recurse(letters, chars):
        if not chars:
            # we're done, check if the word has been found
            for cand in letters:
                if word == str(cand):
                    yield [
                        *((lett.i, lett.j) for lett in cand.path),
                        (cand.i, cand.j)
                    ]
            return

        # singly linked lists would be ideal here, but we're talking about
        #  really short strings, so meh
        # Note that passing iterators is not an option, since we need
        #  to branch in parallel without shared state
        car, cdr = chars[0], chars[1:]
        for letter in letters:
            yield from recurse(branch(letter, car), cdr)

    return recurse(initial_points, word[1:])


def score_word(word, board):
    w_paths = paths(word.replace('QU', 'Q'), board)
    # matters for scoring
    orig_len = len(word)
    try:
        path = next(w_paths)
        if orig_len <= 4:
            score = 1
        elif orig_len == 5:
            score = 2
        elif orig_len == 6:
            score = 3
        elif orig_len == 7:
            score = 5
        else:
            score = 11
        return score, path
    except StopIteration:
        return 0, None


@dataclass(frozen=True)
class WordScore:
    numer_score: int
    duplicate: bool = False
    dictionary_valid: bool = True


def duplicate_entries(itr):
    seen = set()
    for i in itr:
        if i in seen:
            yield i
        seen.add(i)


def score_players(words_by_player, board, dictionary=None):
    # assume words are passed in as Word objects, 
    #  which we modify in-place

    # eliminate duplicates between players
    blacklist = set(
        duplicate_entries(
            map(lambda x: BoggleWord(x.word), chain(*words_by_player))
        )
    )

    no_dict = dictionary is None

    for w in chain(*words_by_player):
        wrapped = BoggleWord(w.word)
        cleaned = str(wrapped)
        score, path = score_word(cleaned, board)
        blacklisted = wrapped in blacklist
        # non-dictionary words do get a nonzero score, since they
        #  may be manually approved.
        # Hence, the "proper" score still needs to be saved in the DB
        w.score = score if not blacklisted else 0
        # path may still be valid, of course
        w.duplicate = blacklisted
        w.dictionary_valid = no_dict or cleaned in dictionary
        print(cleaned, no_dict, w.dictionary_valid)
        w.path_array = json.dumps(path) if path is not None else None


class FileServiceProvider:
    extension = None

    @classmethod
    def name_services(cls, file_name, file_handle):
        """
        Name service(s) in a file in a human-readable way
        :param file_name:
            The file name under consideration
        :param file_handle:
            An open read handle for the service file.
        :return:
            A string, or an iterable of service names (default: the file name)
        """
        return file_name

    @classmethod
    def list_files(cls, directory):
        return glob.iglob(os.path.join(directory, '*.%s' % cls.extension))

    @classmethod
    def discover(cls, directory):
        """
        List available service files in :param directory: without
        importing them, but while still testing whether they're actually
        readable.

        :param directory:
            The directory to read
        :return:
            An iterable with reference names
        """

        files = cls.list_files(directory)
        for fname in files:
            try:
                with open(fname, 'r') as fhandle:
                    names = cls.name_services(fname, fhandle)
                    if isinstance(names, str):
                        # only a single name
                        yield names
                    else:
                        yield from names
            except IOError as e:
                logger.warning(f"Failed to read service file {fname}", e)

    @classmethod
    def read_services(cls, file_name, file_handle):
        """
        Read service(s) in a file

        :param file_name:
            The file name under consideration
        :param file_handle:
            An open read handle for the service file.
        :return:
            An iterable of name - service pairs
            (whatever the service objects are)
        """
        raise NotImplementedError

    @classmethod
    def import_services(cls, directory):
        files = cls.list_files(directory)
        for fname in files:
            logger.info(f"Reading services from {fname}...")
            try:
                with open(fname, 'r') as fhandle:
                    yield from cls.read_services(fname, fhandle)
            except IOError as e:
                logger.warning(f"Failed to read service file {fname}", e)

    def __init__(self, directory):
        self._instances = dict(self.import_services(directory))

    def __getitem__(self, item):
        return self._instances[item]

    def __contains__(self, item):
        return item in self._instances

    def __iter__(self):
        return iter(self._instances)


dictionary_regex = re.compile(r'(.*)\.dic')


class DictionaryServiceProvider(FileServiceProvider):
    extension = 'dic'

    @staticmethod
    def dictionary_name(file_name):
        dictionary_name = dictionary_regex.match(
            os.path.basename(file_name)
        ).group(1)
        return dictionary_name

    @classmethod
    def name_services(cls, file_name, file_handle):
        return DictionaryServiceProvider.dictionary_name(file_name)

    @staticmethod
    def clean_dict(dict_words):
        return {clean_word(word.rstrip()) for word in dict_words}

    @classmethod
    def read_services(cls, file_name, file_handle):
        words = DictionaryServiceProvider.clean_dict(file_handle)
        yield DictionaryServiceProvider.dictionary_name(file_name), words


class DiceConfigServiceProvider(FileServiceProvider):
    extension = 'dice'

    @classmethod
    def read_services(cls, file_name, file_handle):
        reading_dice = False
        name = None
        dice = []
        for line in file_handle:
            line = line.strip()
            if line:
                if reading_dice:
                    dice.extend(line.split(' '))
                else:
                    name = line
                    reading_dice = True
            else:
                # empty line, treat as end of entry
                if dice:
                    yield name, tuple(dice)
                reading_dice = False
                name = None
                dice = []

        # yield last, if applicable
        if dice:
            yield name, tuple(dice)

    @classmethod
    def name_services(cls, file_name, file_handle):
        reading_dice = False
        for line in file_handle:
            line = line.strip()
            if line:
                if not reading_dice:
                    yield line
                    reading_dice = True
            else:
                # empty line, treat as end of entry
                reading_dice = False
