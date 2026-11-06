/* Le vrai executable de SnapActions.app.
 *
 * Un script shell comme executable principal ne suffit pas : macOS attribue
 * alors l'Acces complet au disque au binaire reellement charge — /bin/bash,
 * puis le Python de Xcode — et la case cochee dans les Reglages ne sert a
 * rien. Un binaire natif, signe avec le paquet, donne a l'application une
 * identite stable que TCC sait reconnaitre.
 *
 * Il ne fait presque rien : il trouve python3, lance le serveur comme
 * ENFANT, et attend. Quitter l'application arrete le serveur.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <sys/wait.h>

static pid_t enfant = 0;

static void arreter(int sig) {
    if (enfant > 0) kill(enfant, SIGTERM);
    _exit(0);
}

int main(int argc, char **argv) {
    const char *projet = PROJET;          /* pose a la compilation */
    if (chdir(projet) != 0) {
        fprintf(stderr, "dossier du projet introuvable : %s\n", projet);
        return 1;
    }

    const char *pythons[] = {"/usr/bin/python3", "/usr/local/bin/python3",
                             "/opt/homebrew/bin/python3", NULL};
    const char *py = NULL;
    for (int i = 0; pythons[i]; i++) {
        if (access(pythons[i], X_OK) == 0) { py = pythons[i]; break; }
    }
    if (!py) {
        system("/usr/bin/osascript -e 'display alert \"Python manquant\" "
               "message \"Ouvrez le Terminal et tapez : xcode-select --install\"'");
        return 1;
    }

    /* L'ancien serveur, s'il en reste un. */
    system("/usr/sbin/lsof -ti tcp:8765 2>/dev/null | /usr/bin/xargs -r kill 2>/dev/null");
    sleep(1);

    signal(SIGTERM, arreter);
    signal(SIGINT, arreter);

    char script[2048];
    snprintf(script, sizeof(script), "%s/serveur.py", projet);

    enfant = fork();
    if (enfant < 0) return 1;
    if (enfant == 0) {
        setenv("SNAPACTIONS_LANCEUR", "SnapActions.app", 1);
        char journal[2048];
        snprintf(journal, sizeof(journal), "%s/data/serveur.log", projet);
        freopen(journal, "a", stdout);
        freopen(journal, "a", stderr);
        execl(py, py, script, "serve", (char *)NULL);
        _exit(127);
    }

    int etat = 0;
    waitpid(enfant, &etat, 0);
    return WIFEXITED(etat) ? WEXITSTATUS(etat) : 1;
}
