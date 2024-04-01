"""
LanguageTool.py

This is a simple Sublime Text plugin for checking grammar. It passes buffer
content to LanguageTool (via http) and highlights reported problems.
"""

import fnmatch
import itertools
import json
import os.path
import subprocess

import sublime
import sublime_plugin
from sublime_lib import ActivityIndicator

from . import LTServer


def move_caret(view, i, j):
    """Select character range [i, j] in view."""
    target = view.text_point(0, i)
    view.sel().clear()
    view.sel().add(sublime.Region(target, target + j - i))


def select_problem(view, problem):
    reg = view.get_regions(problem["regionKey"])[0]
    move_caret(view, reg.a, reg.b)
    view.show_at_center(reg)
    show_problem(problem)


def is_problem_solved(view, problem):
    """Return True iff a language problem has been resolved.

    A problem is considered resolved if either:

    1. its region has zero length, or
    2. its contents have been changed.
    """
    rl = view.get_regions(problem["regionKey"])
    assert len(rl) > 0, "tried to find non-existing region"
    region = rl[0]
    return region.empty() or (view.substr(region) != problem["orgContent"])


def show_problem(p):
    """Show problem description and suggestions."""

    def show_problem_panel(p):
        msg = p["message"]
        if p["replacements"]:
            msg += "\n\nSuggestion(s): " + ", ".join(p["replacements"])
        if p["urls"]:
            msg += "\n\nMore Info: " + "\n".join(p["urls"])
        show_panel_text(msg)

    def show_problem_status_bar(p):
        if p["replacements"]:
            msg = "{0} ({1})".format(p["message"], p["replacements"])
        else:
            msg = p["message"]
        sublime.status_message(msg)

    # call appropriate show_problem function

    use_panel = lt_settings().get("display_mode") == "panel"
    show_fun = show_problem_panel if use_panel else show_problem_status_bar
    show_fun(p)


def show_panel_text(text):
    window = sublime.active_window()
    window.run_command("set_language_tool_panel_text", {"str": text})


class SetLanguageToolPanelTextCommand(sublime_plugin.TextCommand):
    def run(self, edit, str):
        window = sublime.active_window()
        pt = window.get_output_panel("languagetool")
        pt.settings().set("wrap_width", 0)
        pt.settings().set("word_wrap", True)
        pt.set_read_only(False)
        pt.run_command("insert", {"characters": str})
        window.run_command("show_panel", {"panel": "output.languagetool"})


class SelectProblemAtCursorCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        v = self.view
        problems = v.settings().get("language_tool_problems", [])
        if len(problems) > 0:
            sel = v.sel()[0]
            for p in problems:
                r = v.get_regions(p["regionKey"])[0]
                if (not is_problem_solved(v, p)) and (r.a < sel.begin() < r.b):
                    select_problem(v, p)


class GotoNextLanguageProblemCommand(sublime_plugin.TextCommand):
    def run(self, edit, jump_forward=True):
        v = self.view
        problems = v.settings().get("language_tool_problems", [])
        if len(problems) > 0:
            sel = v.sel()[0]
            if jump_forward:
                for p in problems:
                    r = v.get_regions(p["regionKey"])[0]
                    if (not is_problem_solved(v, p)) and (sel.begin() < r.a):
                        select_problem(v, p)
                        return
            else:
                for p in reversed(problems):
                    r = v.get_regions(p["regionKey"])[0]
                    if (not is_problem_solved(v, p)) and (r.a < sel.begin()):
                        select_problem(v, p)
                        return
            sublime.status_message("no further language problems to fix")
        sublime.active_window().run_command(
            "hide_panel", {"panel": "output.languagetool"}
        )


class ClearLanguageProblemsCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        v = self.view
        problems = v.settings().get("language_tool_problems", None)
        if problems:
            for p in problems:
                v.erase_regions(p["regionKey"])
            self.view.settings().erase("language_tool_problems")
            recompute_highlights(v)
            caretPos = self.view.sel()[0].end()
            v.sel().clear()
            sublime.active_window().run_command(
                "hide_panel", {"panel": "output.languagetool"}
            )
            move_caret(v, caretPos, caretPos)


class MarkLanguageProblemSolvedCommand(sublime_plugin.TextCommand):
    def run(self, edit, apply_fix):
        v = self.view

        problems = v.settings().get("language_tool_problems", [])
        selected_region = v.sel()[0]

        # Find problem corresponding to selection
        for problem in problems:
            problem_region = v.get_regions(problem["regionKey"])[0]
            if problem_region == selected_region:
                break
        else:
            sublime.status_message("no language problem selected")
            return

        next_caret_pos = problem_region.a
        replacements = problem["replacements"]

        if apply_fix and replacements:
            # fix selected problem:
            self.correct_problem(edit, problem, replacements)

        else:
            # ignore problem:
            equal_problems = get_equal_problems(problems, problem)
            for p2 in equal_problems:
                ignore_problem(p2, v, edit)
            # After ignoring problem:
            move_caret(v, next_caret_pos, next_caret_pos)  # advance caret
            v.run_command("goto_next_language_problem")

    def correct_problem(self, edit, problem, replacements):
        def clear_and_advance():
            clear_region(self.view, problem["regionKey"])
            move_caret(self.view, next_caret_pos, next_caret_pos)  # advance caret
            self.view.run_command("goto_next_language_problem")

        if len(replacements) > 1:

            def callback_fun(i):
                self.choose_suggestion(problem, replacements, i)
                clear_and_advance()

            self.view.window().show_quick_panel(replacements, callback_fun)

        else:
            region = self.view.get_regions(problem["regionKey"])[0]
            self.view.replace(edit, region, replacements[0])
            next_caret_pos = region.a + len(replacements[0])
            clear_and_advance()

    def choose_suggestion(self, p, replacements, choice):
        """Handle suggestion list selection."""
        if choice != -1:
            r = self.view.get_regions(p["regionKey"])[0]
            self.view.run_command("insert", {"characters": replacements[choice]})
            c = r.a + len(replacements[choice])
            move_caret(self.view, c, c)  # move caret to end of region
            self.view.run_command("goto_next_language_problem")
        else:
            select_problem(self.view, p)


def get_equal_problems(problems, x):
    """Find problems with same category and content as a given problem.

    Args:
      problems (list): list of problems to compare.
      x (dict): problem object to compare with.

    Returns:
      list: list of problems equal to x.

    """

    def is_equal(prob1, prob2):
        same_category = prob1["category"] == prob2["category"]
        same_content = prob1["orgContent"] == prob2["orgContent"]
        return same_category and same_content

    return [problem for problem in problems if is_equal(problem, x)]


def lt_settings():
    return sublime.load_settings("LanguageTool.sublime-settings")


class StartLanguageToolServerCommand(sublime_plugin.TextCommand):
    """Launch local LanguageTool Server."""

    def run(self, edit):
        jar_path = lt_settings().get("languagetool_jar")

        if not jar_path:
            show_panel_text("Setting languagetool_jar is undefined")
            return

        if not os.path.isfile(jar_path):
            show_panel_text(
                "Error, could not find LanguageTool's JAR file (%s)"
                "\n\n"
                "Please install LT in this directory"
                " or modify the `languagetool_jar` setting." % jar_path
            )
            return

        sublime.status_message("Starting local LanguageTool server ...")

        cmd = [
            "java",
            "-cp",
            jar_path,
            "org.languagetool.server.HTTPServer",
            "--port",
            "8081",
        ]

        if sublime.platform() == "windows":
            p = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=True,
                creationflags=subprocess.SW_HIDE,
            )
        else:
            p = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )


class ChangeLanguageToolLanguageCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        server_url = get_server_url(lt_settings(), False)
        response = LTServer.getLanguages(server_url)
        languages = [("Autodetect Language", "auto")] + sorted(
            set((lang["name"], lang["longCode"]) for lang in response)
        )
        self.view.settings().set("language_tool_languages", languages)

        languageNames = [lang[0] for lang in languages]
        self.view.window().show_quick_panel(
            languageNames, self.handle_language_selection
        )

    def handle_language_selection(self, index):
        key = "language_tool_language"
        if index == 0:
            self.view.settings().erase(key)
        else:
            languages = self.view.settings().get("language_tool_languages")
            selected_language = languages[index][1]
            self.view.settings().set(key, selected_language)


def clear_region(view, region_key):
    r = view.get_regions(region_key)[0]
    dummyRg = sublime.Region(r.a, r.a)
    hscope = lt_settings().get("highlight-scope", "comment")
    view.add_regions(region_key, [dummyRg], hscope, "", sublime.DRAW_OUTLINED)


def ignore_problem(p, v, edit):
    clear_region(v, p["regionKey"])
    v.insert(edit, v.size(), "")  # dummy edit to enable undoing ignore


def load_ignored_rules():
    ignored_rules_file = "LanguageToolUser.sublime-settings"
    settings = sublime.load_settings(ignored_rules_file)
    return settings.get("ignored", [])


def save_ignored_rules(ignored):
    ignored_rules_file = "LanguageToolUser.sublime-settings"
    settings = sublime.load_settings(ignored_rules_file)
    settings.set("ignored", ignored)
    sublime.save_settings(ignored_rules_file)


def get_server_url(settings, force_server):
    """Return LT server url based on settings.

    The returned url is for either the local or remote servers, defined by the
    settings entries:

        - language_server_local
        - language_server_remote

    The choice between the above is made based on the settings value
    'default_server'. If not None, `force_server` will override this setting.

    """
    server_setting = force_server or settings.get("default_server")
    setting_name = "languagetool_server_%s" % server_setting
    server = settings.get(setting_name)
    return server


class LanguageToolCommand(sublime_plugin.TextCommand):
    def run(self, edit, force_server=None):
        sublime.set_timeout_async(lambda: self.check_text(force_server))

    def get_text(self, check_region):
        spelling_selector = self.view.settings().get("spelling_selector")

        chunks = []
        current_chunk = ""
        current_type = None
        for span, scope in self.view.extract_tokens_with_scopes(check_region):
            chunk_type = sublime.score_selector(scope, spelling_selector)
            if chunk_type == current_type:
                current_chunk += self.view.substr(span)
            else:
                if current_type == 0:
                    type_name = "markup"
                else:
                    type_name = "text"
                if current_chunk:
                    chunks.append({type_name: current_chunk})
                current_type = chunk_type
                current_chunk = self.view.substr(span)
        if current_type == 0:
            type_name = "markup"
        else:
            type_name = "text"
        if current_chunk:
            chunks.append({type_name: current_chunk})

        return json.dumps({"annotation": chunks})

    def check_text(self, force_server):
        settings = lt_settings()
        server_url = get_server_url(settings, force_server)
        ignored_scopes = settings.get("ignored-scopes")
        highlight_scope = settings.get("highlight-scope")

        selection = self.view.sel()[0]  # first selection (ignore rest)
        everything = sublime.Region(0, self.view.size())
        check_region = everything if selection.empty() else selection
        check_text = self.get_text(check_region)

        self.view.run_command("clear_language_problems")

        language = self.view.settings().get("language_tool_language", "auto")
        username = settings.get("username", "")
        apikey = settings.get("apikey", "")

        ignored_ids = [rule["id"] for rule in load_ignored_rules()]

        before = self.view.change_id()
        with ActivityIndicator(self.view, "LanguageTool"):
            matches = LTServer.getResponse(
                server_url, check_text, language, ignored_ids, username, apikey
            )

        if matches is None:
            return

        check_region = self.view.transform_region_from(check_region, before)

        def get_region(problem):
            """Return a Region object corresponding to problem text."""
            length = problem["length"]
            offset = problem["offset"]
            region = sublime.Region(offset, offset + length)
            region = self.view.transform_region_from(region, before)
            return region

        def inside(problem):
            """Return True iff problem text is inside check_region."""
            region = get_region(problem)
            return check_region.contains(region)

        def is_ignored(problem):
            """Return True iff any problem scope is ignored."""
            scope_string = self.view.scope_name(problem["offset"])
            scopes = scope_string.split()
            return cross_match(scopes, ignored_scopes, fnmatch.fnmatch)

        def add_highlight_region(region_key, problem):
            region = get_region(problem)
            problem["orgContent"] = self.view.substr(region)
            problem["regionKey"] = region_key

            # style = sublime.DRAW_SQUIGGLY_UNDERLINE + sublime.DRAW_NO_FILL + sublime.DRAW_NO_OUTLINE
            style = sublime.DRAW_NO_FILL
            self.view.add_regions(
                region_key,
                [region],
                highlight_scope,
                "",
                style,
            )

        shifter = lambda problem: shift_offset(problem, check_region.a)

        get_problem = compose(shifter, parse_match)

        problems = [
            problem
            for problem in map(get_problem, matches)
            if inside(problem) and not is_ignored(problem)
        ]

        for index, problem in enumerate(problems):
            add_highlight_region(str(index), problem)

        if problems:
            select_problem(self.view, problems[0])
        else:
            sublime.status_message("no language problems were found :-)")

        self.view.settings().set("language_tool_problems", problems)


def compose(f1, f2):
    """Compose two functions."""

    def inner(*args, **kwargs):
        return f1(f2(*args, **kwargs))

    return inner


def cross_match(list1, list2, predicate):
    """Cross match items from two lists using a predicate.

    Args:
      list1 (list): list 1.
      list2 (list): list 2.

    Returns:
      True iff predicate(x, y) is True for any x in list1 and y in list2,
      False otherwise.

    """
    return any(predicate(x, y) for x, y in itertools.product(list1, list2))


def shift_offset(problem, shift):
    """Shift problem offset by `shift`."""

    problem["offset"] += shift
    return problem


def parse_match(match):
    """Parse a match object.

    Args:
      match (dict): match object returned by LanguageTool Server.

    Returns:
      dict: problem object.

    """

    problem = {
        "category": match["rule"]["category"]["name"],
        "message": match["message"],
        "replacements": [r["value"] for r in match["replacements"]],
        "rule": match["rule"]["id"],
        "urls": [w["value"] for w in match["rule"].get("urls", [])],
        "offset": match["offset"],
        "length": match["length"],
    }

    return problem


class DeactivateRuleCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        ignored = load_ignored_rules()
        v = self.view
        problems = v.settings().get("language_tool_problems", [])
        sel = v.sel()[0]
        selected = [
            p for p in problems if sel.contains(v.get_regions(p["regionKey"])[0])
        ]
        if not selected:
            sublime.status_message("select a problem to deactivate its rule")
        elif len(selected) == 1:
            rule = {"id": selected[0]["rule"], "description": selected[0]["message"]}
            ignored.append(rule)
            ignoredProblems = [p for p in problems if p["rule"] == rule["id"]]
            for p in ignoredProblems:
                ignore_problem(p, v, edit)
            problems = [p for p in problems if p["rule"] != rule["id"]]
            v.run_command("goto_next_language_problem")
            save_ignored_rules(ignored)
            sublime.status_message("deactivated rule %s" % rule)
        else:
            sublime.status_message(
                "there are multiple selected problems;" " select only one to deactivate"
            )


class ActivateRuleCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        ignored = load_ignored_rules()
        if ignored:
            activate_callback_wrapper = lambda i: self.activate_callback(i)
            ruleList = [[rule["id"], rule["description"]] for rule in ignored]
            self.view.window().show_quick_panel(ruleList, activate_callback_wrapper)
        else:
            sublime.status_message("there are no ignored rules")

    def activate_callback(self, i):
        ignored = load_ignored_rules()
        if i != -1:
            activate_rule = ignored[i]
            ignored.remove(activate_rule)
            save_ignored_rules(ignored)
            sublime.status_message("activated rule %s" % activate_rule["id"])


class LanguageToolListener(sublime_plugin.EventListener):
    def on_modified(self, view):
        # buffer text was changed, recompute region highlights
        recompute_highlights(view)


def recompute_highlights(view):
    problems = view.settings().get("language_tool_problems", [])
    print(problems)
    hscope = lt_settings().get("highlight-scope", "comment")
    for p in problems:
        rL = view.get_regions(p["regionKey"])
        if rL:
            regionScope = "" if is_problem_solved(view, p) else hscope
            view.add_regions(p["regionKey"], rL, regionScope, "", sublime.DRAW_OUTLINED)
