"""
Hacker / hacking-culture quotes shown randomly at CLI startup.

Used by navig/cli/_callbacks.py  →  _get_hacker_quotes()
and      navig/cli/__init__.py   →  _get_hacker_quotes()

Each entry is a (quote, attribution) tuple.
"""

from __future__ import annotations

HACKER_QUOTES: list[tuple[str, str]] = [
    ("The best way to predict the future is to invent it.", "Alan Kay"),
    ("Any sufficiently advanced technology is indistinguishable from magic.", "Arthur C. Clarke"),
    ("A computer lets you make more mistakes faster than any other invention.", "Mitch Ratcliffe"),
    ("Programs must be written for people to read, and only incidentally for machines to execute.", "Harold Abelson"),
    ("The Internet is becoming the town square for the global village of tomorrow.", "Bill Gates"),
    ("Measuring programming progress by lines of code is like measuring aircraft building progress by weight.", "Bill Gates"),
    ("Software is eating the world.", "Marc Andreessen"),
    ("Move fast and break things.", "Mark Zuckerberg"),
    ("It's not a bug — it's an undocumented feature.", "Anonymous"),
    ("Talk is cheap. Show me the code.", "Linus Torvalds"),
    ("Given enough eyeballs, all bugs are shallow.", "Eric S. Raymond"),
    ("The most damaging phrase in the language is 'We've always done it this way.'", "Grace Hopper"),
    ("The question of whether a computer can think is no more interesting than the question of whether a submarine can swim.", "Edsger W. Dijkstra"),
    ("Simplicity is the ultimate sophistication.", "Leonardo da Vinci"),
    ("First, solve the problem. Then, write the code.", "John Johnson"),
    ("Make it work, make it right, make it fast.", "Kent Beck"),
    ("Code is like humor. When you have to explain it, it's bad.", "Cory House"),
    ("Programming today is a race between software engineers striving to build bigger and better idiot-proof programs, and the Universe trying to produce bigger and better idiots. So far, the Universe is winning.", "Rick Cook"),
    ("There are only two hard things in Computer Science: cache invalidation and naming things.", "Phil Karlton"),
    ("In theory, theory and practice are the same. In practice, they are not.", "Benjamin Brewster"),
    ("The computer was born to solve problems that did not exist before.", "Bill Gates"),
    ("Computers are good at following instructions, but not at reading your mind.", "Donald Knuth"),
    ("The only way to learn a new programming language is by writing programs in it.", "Dennis Ritchie"),
    ("An expert is a person who has found out by his own painful experience all the mistakes that one can make in a very narrow field.", "Niels Bohr"),
    ("Simplicity carried to the extreme becomes elegance.", "Jon Franklin"),
    ("The strength of JavaScript is that you can do anything. The weakness is that you will.", "Reg Braithwaite"),
    ("Walking on water and developing software from a specification are easy if both are frozen.", "Edward V. Berard"),
    ("Always code as if the guy who ends up maintaining your code will be a violent psychopath who knows where you live.", "John Woods"),
    ("The best code is no code at all.", "Jeff Atwood"),
    ("Debugging is like being the detective in a crime movie where you are also the murderer.", "Filipe Fortes"),
    ("There are two ways to write error-free programs; only the third one works.", "Alan J. Perlis"),
    ("In programming, if someone tells you 'you're overcomplicating things,' they're either wrong or you need to stop.", "Anonymous"),
    ("The computer is incredibly fast, accurate, and stupid. Man is unbelievably slow, inaccurate, and brilliant.", "Leo Cherne"),
    ("Knowledge is power.", "Francis Bacon"),
    ("Hackers are arrogant geek romantics. They lack the social graces that smooth over an uneven world.", "Douglas Rushkoff"),
    ("Security is always excessive until it's not enough.", "Robbie Sinclair"),
    ("Privacy is not something that I'm merely entitled to, it's an absolute prerequisite.", "Marlon Brando"),
    ("With great power comes great responsibility.", "Voltaire / Stan Lee"),
    ("It takes 20 years to build a reputation and five minutes to ruin it.", "Warren Buffett"),
    ("Don't comment bad code — rewrite it.", "Brian W. Kernighan"),
]
