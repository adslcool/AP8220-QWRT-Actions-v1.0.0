/* SPDX-License-Identifier: ISC
 * Run under the original firmware loader/libc. No account files are accessed.
 * Password and encoded hash arrive on stdin; neither is printed.
 */
#include <crypt.h>
#include <stdio.h>
#include <string.h>

int main(void)
{
	char password[1024], encoded[256];
	if (!fgets(password, sizeof(password), stdin) ||
	    !fgets(encoded, sizeof(encoded), stdin))
		return 2;
	password[strcspn(password, "\n")] = '\0';
	encoded[strcspn(encoded, "\n")] = '\0';
	char *actual = crypt(password, encoded);
	return !actual || strcmp(actual, encoded);
}
