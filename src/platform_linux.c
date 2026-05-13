/* NI Linux RT platform lifecycle: shared library constructor/destructor.
 * No special initialization is required on Linux — pthreads and AF_PACKET
 * are always available without setup. */

__attribute__((constructor))
static void pn_lib_init(void) {}

__attribute__((destructor))
static void pn_lib_fini(void) {}
