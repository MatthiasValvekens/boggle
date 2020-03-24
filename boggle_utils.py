import random
import json
from dataclasses import dataclass, field
from itertools import islice, chain
import os
import logging
import glob
import re


logger = logging.getLogger(__name__)

DICE_CONFIG = (
    'ETUKNO', 'EVGTIN', 'DECAMP', 'IELRUW',
    'EHIFSE', 'RECALS', 'ENTDOS', 'OFXRIA',
    'NAVEDZ', 'EIOATA', 'GLENYU', 'BMAQJO',
    'TLIBRA', 'SPULTE', 'AIMSOR', 'ENHRIS'
)


def roll(seed, board_dims=(4, 4), dice_config=DICE_CONFIG):
    rng = random.Random(seed)
    random_dice = rng.sample(dice_config, len(dice_config))
    flat_board = [
        die[rng.randrange(len(die))] for die in random_dice
    ]
    board_iter = iter(flat_board)
    rows, cols = board_dims
    return [[ch for ch in islice(board_iter, cols)] for _ in range(rows)]


class BoggleWord:
    """
    Utility wrapper to force Boggle words to be upper case, and compare
    with the QU -> Q substitution.
    """
    def __init__(self, word):
        self.word = word = word.upper()
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
        return []

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
        duplicate_entries(map(lambda x: x.word, chain(*words_by_player)))
    )

    no_dict = dictionary is None

    for w in chain(*words_by_player):
        score, path = score_word(w.word, board)
        blacklisted = w.word in blacklist
        # non-dictionary words do get a nonzero score, since they
        #  may be manually approved (TODO)
        w.score = score if not blacklisted else 0,
        # path may still be valid, of course
        # in that case, we wasted a tiny bit of resources
        w.duplicate = blacklisted
        w.dictionary_valid = no_dict or w.word in dictionary
        w.path_array = json.dumps(path)


dictionary_regex = re.compile(r'(.*)\.dic')


class DictionaryService:

    @staticmethod
    def list_dictionaries(dictionary_dir):
        """
        List available dictionaries in :param dictionary_dir: without
        importing them.

        :param dictionary_dir:
            The directory to read
        :return:
            A generator yielding base name - file name pairs
        """
        files = glob.iglob(os.path.join(dictionary_dir, '*.dic'))
        for fname in files:
            dictionary_name = dictionary_regex.match(
                os.path.basename(fname)
            ).group(1)
            yield dictionary_name, fname

    @staticmethod
    def read_dictionaries(dictionary_dir):
        dicts = DictionaryService.list_dictionaries(dictionary_dir)
        for dictionary_name, fname in dicts:
            logger.info(f"Importing dictionary {fname}...")
            try:
                words = {word.rstrip().upper() for word in open(fname, 'r')}
                yield dictionary_name, words
            except IOError as e:
                logger.warning(f"Failed to read dictionary {fname}", e)

    def __init__(self, dictionary_dir):
        self.__dictionaries = dict(
            DictionaryService.read_dictionaries(dictionary_dir)
        )

    def __getitem__(self, item):
        return self.__dictionaries[item]

    def __contains__(self, item):
        return item in self.__dictionaries

    def __iter__(self):
        return iter(self.__dictionaries)
