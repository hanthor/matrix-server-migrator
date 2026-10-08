/* Wait for a Linux process to exit, including transition to an unreaped
 * zombie. A pidfd avoids polling and cannot follow a recycled PID. */
#define _GNU_SOURCE
#include <errno.h>
#include <limits.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/syscall.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    char *end;
    errno = 0;
    long pid = strtol(argv[1], &end, 10);
    if (errno || *end || pid <= 0 || pid > INT_MAX) return 2;
    int fd = syscall(SYS_pidfd_open, (int)pid, 0);
    if (fd < 0) {
        if (errno == ESRCH) return 0;
        perror("pidfd_open");
        return 2;
    }
    struct pollfd watch = { .fd = fd, .events = POLLIN };
    int result;
    do { result = poll(&watch, 1, -1); } while (result < 0 && errno == EINTR);
    close(fd);
    return result > 0 && (watch.revents & POLLIN) ? 0 : 2;
}
